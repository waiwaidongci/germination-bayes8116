from sqlalchemy import Text,JSON,Integer,ForeignKey,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from ..config.database import Base
from ..utils.clock import now
class BootstrapFit(Base):
    __tablename__="bootstrap_fits"
    id:Mapped[int]=mapped_column(primary_key=True)
    cache_key:Mapped[str]=mapped_column(unique=True,index=True)
    data_hash:Mapped[str]
    seed:Mapped[int]=mapped_column(Integer)
    reps:Mapped[int]=mapped_column(Integer)
    status:Mapped[str]
    fits:Mapped[dict]=mapped_column(JSON)
    study_id:Mapped[int]=mapped_column(Integer,nullable=True,default=None)
    created_at:Mapped[str]=mapped_column(default=now)
