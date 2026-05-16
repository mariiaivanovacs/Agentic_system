"""
Creates the SQLite startups database for local dev testing.
Run once: python mock_infrastructure/setup_mock_data.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "startups.db")


def create_startups_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS startups")
    c.execute(
        """CREATE TABLE startups (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            industry TEXT,
            stage TEXT,
            founder_email TEXT
        )"""
    )
    rows = [
        (1,  "PayFast",      "Fintech",        "Seed",      "ceo@payfast.com"),
        (2,  "HealthAI",     "Healthtech",     "Series A",  "founder@healthai.com"),
        (3,  "ShopEasy",     "E-commerce",     "Pre-seed",  None),
        (4,  "CryptoKing",   "FinTech",        "Seed",      "info@crypto.com"),
        (5,  "NeuralMind",   "AI",             "Seed",      "hello@neuralmind.io"),
        (6,  "LoanBridge",   "Financial Tech", "Series A",  "ops@loanbridge.com"),
        (7,  "RegShield",    "fintech",        "Pre-seed",  "team@regshield.com"),
        (8,  "TalkBot",      "AI",             "Seed",      None),
        (9,  "CloudShift",   "SaaS",           "Series A",  "founders@cloudshift.io"),
        (10, "QuickCart",    "E-Commerce",     "Pre-seed",  "cto@quickcart.com"),
    ]
    c.executemany("INSERT INTO startups VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    print(f"SQLite DB created at: {DB_PATH}")


if __name__ == "__main__":
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    create_startups_db()
