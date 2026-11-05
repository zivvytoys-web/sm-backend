from datetime import datetime
from sqlalchemy import Column, Integer, DateTime
from sqlalchemy.orm import declarative_base, declared_attr


class CustomBase:
    @declared_attr
    def __tablename__(cls) -> str:
        # Default table name is lowercase class name pluralized
        return cls.__name__.lower() + "s"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


Base = declarative_base(cls=CustomBase)
