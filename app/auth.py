from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from app.config import get_settings

bearer = HTTPBearer()


def _decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256", "HS512"])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido o expirado")


async def require_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> str:
    payload = _decode_token(creds.credentials)
    email: str | None = payload.get("sub")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    return email


async def require_admin(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> str:
    payload = _decode_token(creds.credentials)
    email: str | None = payload.get("sub")
    role: str | None = payload.get("role")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    if role != "ROLE_ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso solo para administradores")
    return email
