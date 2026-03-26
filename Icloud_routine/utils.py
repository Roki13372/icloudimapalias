import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent
PROFILES = ROOT / "profiles.xlsx"
RUNTIME = ROOT / "runtime"
RUNTIME.mkdir(parents=True, exist_ok=True)
(RUNTIME / "logs").mkdir(parents=True, exist_ok=True)

def load_profiles() -> pd.DataFrame:
    if not PROFILES.exists():
        df = pd.DataFrame(columns=["Profile ID", "Alias Email", "Main Email", "Password", "Status"])
        df.to_excel(PROFILES, index=False)
    df = pd.read_excel(PROFILES)
    return df.fillna("")

def save_profiles(df: pd.DataFrame):
    df.to_excel(PROFILES, index=False)