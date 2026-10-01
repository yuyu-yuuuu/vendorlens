from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from vendorlens import __version__
from vendorlens.db import get_session

app = FastAPI(title="VendorLens API", version=__version__)


DatabaseSession = Annotated[Session, Depends(get_session)]


@app.get("/health")
def health(session: DatabaseSession) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__, "database": "ok"}
