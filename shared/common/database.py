import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_session_factory(database_name: str) -> sessionmaker[Session]:
    database_url = os.getenv("DATABASE_URL", f"sqlite:///./{database_name}.db")
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db(session_factory: sessionmaker[Session]):
    def dependency() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    return dependency
