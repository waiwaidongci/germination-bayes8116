from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .settings import DATABASE_URL
class Base(DeclarativeBase):
    pass
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
def ensure_schema(engine):
    """Add columns introduced after the first release so old databases keep working."""
    from sqlalchemy import inspect, text
    inspector=inspect(engine)
    if not inspector.has_table("bayes_runs"):return
    existing={column["name"] for column in inspector.get_columns("bayes_runs")}
    with engine.begin() as connection:
        if "bootstrap_seed" not in existing:connection.execute(text("ALTER TABLE bayes_runs ADD COLUMN bootstrap_seed INTEGER"))
        if "bootstrap_reps" not in existing:connection.execute(text("ALTER TABLE bayes_runs ADD COLUMN bootstrap_reps INTEGER"))
def get_db():
    with SessionLocal() as db:
        yield db
