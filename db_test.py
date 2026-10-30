import mysql.connector
from dotenv import load_dotenv
import os

load_dotenv()

try:
    required_settings = ("DB_USER", "DB_PASSWORD", "DB_NAME")
    missing_settings = [name for name in required_settings if not os.getenv(name)]
    if missing_settings:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_settings)}"
        )

    connection = mysql.connector.connect(
        host=os.getenv("DB_HOST") or "127.0.0.1",
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME")
    )

    print("MySQL Database Connected Successfully!")

    connection.close()

except Exception as e:
    print("Connection Error:", e)