import copernicusmarine

# Get sound velocity profile data for acoustic link modeling
# This is what you use to make UUV comms realistic
df = copernicusmarine.read_dataframe(
    dataset_id="cmems_mod_med_phy_anfc_4.2km_P1D-m",
    variables=["thetao"],  # ocean temperature
    minimum_latitude=37.8,
    maximum_latitude=38.5,
    minimum_longitude=15.2,
    maximum_longitude=15.8,
    start_datetime="2026-06-13",
    end_datetime="2026-06-13",
)
print(df.head())