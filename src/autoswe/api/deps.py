"""FastAPI dependencies: application services and the authenticated principal."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from autoswe.config import Settings
from autoswe.db import Database
from autoswe.events import JobNotifier
from autoswe.llm.registry import ProviderRegistry
from autoswe.repository import JobRepository
from autoswe.security import ApiKeyAuthenticator, Principal, extract_api_key
from autoswe.worker import Worker


@dataclass
class Services:
    settings: Settings
    db: Database
    registry: ProviderRegistry
    notifier: JobNotifier
    auth: ApiKeyAuthenticator
    worker: Worker | None


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


async def get_session(services: ServicesDep) -> AsyncIterator[AsyncSession]:
    async with services.db.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_repository(session: SessionDep) -> JobRepository:
    return JobRepository(session)


RepositoryDep = Annotated[JobRepository, Depends(get_repository)]


def get_principal(
    services: ServicesDep,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    principal = services.auth.authenticate(extract_api_key(authorization, x_api_key))
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]
