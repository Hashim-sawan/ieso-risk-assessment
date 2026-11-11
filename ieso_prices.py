# IESO day-ahead vs real-time price analysis
# it re-downloads everything each time, so give it a few minutes
import io
import datetime as dt
import xml.etree.ElementTree as ET

import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

BASE = "https://reports-public.ieso.ca/public"
NS = {"i": "http://www.ieso.ca/schema"}

# 1. figure out which dates to use
today = dt.date.today()
end = today - dt.timedelta(days=today.weekday() + 1)   # last sunday
start = end - dt.timedelta(days=27)                    # back to the monday 4 weeks before
days = pd.date_range(start, end)
print(f"Period: {start} to {end}")

# so i'm not repeating requests.get
def get(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content

# download the prices
# one file per day, hourly prices in each
da_list = []
for d in days:
    url = f"{BASE}/DAHourlyOntarioZonalPrice/PUB_DAHourlyOntarioZonalPrice_{d:%Y%m%d}.xml"
    df = pd.read_xml(io.BytesIO(get(url)), xpath="//i:HourlyPriceComponents", namespaces=NS)
    df["time"] = d + pd.to_timedelta(df["PricingHour"] - 1, unit="h")
    da_list.append(df[["time", "ZonalPrice"]])
da = pd.concat(da_list, ignore_index=True).rename(columns={"ZonalPrice": "da_price"})

# one per hour with 12 five-min prices each
rt_list = []
for d in days:
    for hour in range(1, 25):
        url = f"{BASE}/RealtimeOntarioZonalPrice/PUB_RealtimeOntarioZonalPrice_{d:%Y%m%d}{hour:02d}.xml"
        df = pd.read_xml(io.BytesIO(get(url)), xpath="//i:ZonalPrice", namespaces=NS)
        df["time"] = d + pd.Timedelta(hours=hour - 1) + pd.to_timedelta((df["Interval"] - 1) * 5, unit="min")
        rt_list.append(df[["time", "LmpCap"]])
    print("  downloaded", d.date())   # so i know it's not stuck
rt5 = pd.concat(rt_list, ignore_index=True).rename(columns={"LmpCap": "rt_price"})

# demand and generation by fuel
dem = pd.read_csv(io.BytesIO(get(f"{BASE}/Demand/PUB_Demand_{end.year}.csv")), skiprows=3)
dem["time"] = pd.to_datetime(dem["Date"]) + pd.to_timedelta(dem["Hour"] - 1, unit="h")
# market demand includes exports, so this removes them
dem["exports"] = dem["Market Demand"] - dem["Ontario Demand"]

# the fuel file is nested xml so read_xml can't handle it, must loop through it manually
root = ET.fromstring(get(f"{BASE}/GenOutputbyFuelHourly/PUB_GenOutputbyFuelHourly_{end.year}.xml"))
rows = []
for day in root.iterfind(".//i:DailyData", NS):
    date = pd.Timestamp(day.find("i:Day", NS).text)
    for hr in day.iterfind("i:HourlyData", NS):
        time = date + pd.Timedelta(hours=int(hr.find("i:Hour", NS).text) - 1)
        for fuel in hr.iterfind("i:FuelTotal", NS):
            output = fuel.find(".//i:Output", NS)
            rows.append([time, fuel.find("i:Fuel", NS).text, float(output.text) if output is not None else 0])
gen = pd.DataFrame(rows, columns=["time", "fuel", "mw"]).pivot_table(index="time", columns="fuel", values="mw")

# average the 5 min realtime prices into hours and join everything into one table
rt5["hour_start"] = rt5["time"].dt.floor("h")
rt_hourly = rt5.groupby("hour_start")["rt_price"].agg(rt_price="mean", rt_max="max").reset_index()
df = (da.merge(rt_hourly, left_on="time", right_on="hour_start")
        .merge(dem[["time", "Ontario Demand", "Market Demand", "exports"]], on="time")
        .merge(gen, left_on="time", right_index=True)
        .drop(columns="hour_start"))
df["spread"] = df["rt_price"] - df["da_price"]   # positive = rt came in higher than da
df["hour_ending"] = df["time"].dt.hour + 1
# whatever nuclear, wind and solar don't cover has to come from gas, hydro or imports
df["residual"] = df["Ontario Demand"] - df["NUCLEAR"] - df["WIND"] - df["SOLAR"]
# save the tables so i can double check things in excel
df.to_csv("hourly_data.csv", index=False)
df.set_index("time").resample("D").mean(numeric_only=True).to_csv("daily_data.csv")
rt5[["time", "rt_price"]].to_csv("realtime_5min.csv", index=False)

# the numbers i used in the write-up
print(f"\nDA average  {df.da_price.mean():.2f}  std {df.da_price.std():.1f}")
print(f"RT average  {df.rt_price.mean():.2f}  std {df.rt_price.std():.1f}")
print(f"Average |RT - DA|        {df.spread.abs().mean():.2f}")
print(f"Hours with RT below DA   {(df.spread < 0).mean():.0%}")
spikes = rt5[rt5.rt_price > 200]
# HE17-19 is hours 16-18 on python's 0-23 clock
print(f"5-min prices above $200  {len(spikes)}  ({spikes.time.dt.hour.between(16, 18).sum()} in HE17-19)")
print("Highest 5-min prices:\n", rt5.nlargest(5, "rt_price")[["time", "rt_price"]].to_string(index=False))
print("Correlation with residual demand: DA",
      round(df.residual.corr(df.da_price), 2), " RT", round(df.residual.corr(df.rt_price), 2))
print("Average RT - DA by hour ending:\n", df.groupby("hour_ending").spread.mean().round(1).to_string())

# charts
DA_COLOR, RT_COLOR = "#246dc5", "#eb6834"
plt.rcParams.update({"font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": "#e1e0d9", "axes.titleweight": "bold",
                     "axes.titlelocation": "left"})

# prices over time, the average day, and the heatmap
fig, axes = plt.subplot_mosaic([["top", "top"], ["left", "right"]], figsize=(7.5, 9.6), layout="constrained")
ax1, ax2, ax3 = axes["top"], axes["left"], axes["right"]
ax1.plot(df.time, df.da_price, color=DA_COLOR, lw=1, label="Day-ahead (hourly)")
ax1.plot(df.time, df.rt_price, color=RT_COLOR, lw=1, label="Real-time (hourly average)")
ax1.scatter(spikes.time, spikes.rt_price, s=8, color=RT_COLOR, label="Real-time 5-min price > $200")
top = rt5.loc[rt5.rt_price.idxmax()]   # label the biggest spike
ax1.annotate(f"${top.rt_price:,.0f} on {top.time:%a %b %d %H:%M}", (top.time, top.rt_price),
             xytext=(6, 0), textcoords="offset points", fontsize=7)
# linear up to $150 then log, otherwise the $1,133 spike squishes everything
ax1.set_yscale("symlog", linthresh=150)
ax1.set_yticks([0, 50, 100, 150, 300, 600, 1200], ["0", "50", "100", "150", "300", "600", "1200"])
ax1.set_ylabel("$/MWh")
ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
ax1.legend(loc="upper right", fontsize=7)
ax1.set_title(f"1. Ontario zonal price, day-ahead vs real-time, {start:%b %d} to {end:%b %d, %Y}")

by_hour = df.groupby("hour_ending")
ax2.plot(by_hour.da_price.mean(), color=DA_COLOR, marker="o", ms=3, label="Day-ahead")
ax2.plot(by_hour.rt_price.mean(), color=RT_COLOR, marker="o", ms=3, label="Real-time")
ax2.set_xticks([1, 4, 8, 12, 16, 20, 24])
ax2.set_xlabel("Hour ending (EST)")
ax2.set_ylabel("Average $/MWh")
ax2.legend()
ax2.set_title("2. Average price by hour of day")

# rt minus da for every day and hour
grid = df.pivot_table(index="hour_ending", columns=df.time.dt.date, values="spread")
im = ax3.imshow(grid.clip(-50, 50), aspect="auto", origin="lower", cmap="RdBu_r", vmin=-50, vmax=50,
                extent=[0, grid.shape[1], 0.5, 24.5])
ax3.set_xticks(range(0, grid.shape[1], 7), [f"{d:%b %d}" for d in grid.columns[::7]])
ax3.set_ylabel("Hour ending (EST)")
ax3.grid(False)
fig.colorbar(im, ax=ax3, label="RT minus DA, $/MWh (clipped at ±50)")
ax3.set_title("3. RT minus DA by day and hour")
fig.savefig("page_a_prices.png", dpi=200)

# daily fuel mix and price vs residual demand
fig, (ax4, ax5) = plt.subplots(1, 2, figsize=(7.5, 3.6), layout="constrained")
daily = df.set_index("time").resample("D").mean(numeric_only=True)
fuels = ["NUCLEAR", "HYDRO", "GAS", "WIND", "SOLAR"]
ax4.stackplot(daily.index, [daily[f] for f in fuels], labels=[f.title() for f in fuels],
              colors=["#6250d6", "#1baf7a", "#e34948", "#2a78d6", "#eda100"])
ax4.plot(daily.index, daily["Ontario Demand"], color="black", lw=1.2, label="Ontario demand")
ax4.set_ylabel("Average MW")
ax4.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
ax4.tick_params(axis="x", rotation=45)
ax4.legend(fontsize=6.5, loc="lower left", ncol=2)
ax4.set_title("4. Daily generation by fuel")

ax5.scatter(df.residual, df.da_price, s=5, alpha=0.5, color=DA_COLOR, label="Day-ahead")
ax5.scatter(df.residual, df.rt_price, s=5, alpha=0.5, color=RT_COLOR, label="Real-time")
ax5.set_ylim(-10, 300)
ax5.set_xlabel("Residual demand, MW\n(Ontario demand − nuclear − wind − solar)")
ax5.set_ylabel("$/MWh")
ax5.legend()
ax5.set_title("5. Price vs residual demand")
fig.savefig("page_b_drivers.png", dpi=200)
print("\nSaved page_a_prices.png, page_b_drivers.png, hourly_data.csv, daily_data.csv, realtime_5min.csv")