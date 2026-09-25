import os
from sqlalchemy import create_engine

# pgsql connection on EC2
def get_db_engine():
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASSWORD")
    db_host = os.getenv("DB_HOST")
    db_port = os.getenv("DB_PORT")
    db_name = os.getenv("DB_NAME")
    
    if db_host:
        DB_URL = f"postgresql+psycopg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    else:
        DB_URL = os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:password@localhost:5432/clinical_db")
    
    return create_engine(DB_URL)

