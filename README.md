# IESO Day-Ahead vs Real-Time Ontario Price Analysis

One script, `ieso_prices.py` (pandas + matplotlib).

    pip install pandas matplotlib requests lxml
    python ieso_prices.py                          # last 4 full Mon-Sun weeks
    python ieso_prices.py 2026-09-14 2026-10-11    # any start and end date

A run takes about 4 minutes, because it downloads one real-time file per hour. It saves:
- page_a_prices.png: Figures 1-3 (prices over time, average by hour, RT minus DA heatmap)
- page_b_drivers.png: Figures 4-5 (daily generation by fuel, price vs residual demand)
- hourly_data.csv, daily_data.csv, realtime_5min.csv

Data comes from reports-public.ieso.ca/public: DAHourlyOntarioZonalPrice, RealtimeOntarioZonalPrice, Demand, GenOutputbyFuelHourly.
All times are EST, hour ending (HE1 = 00:00-01:00). Exports = market demand - Ontario demand.
