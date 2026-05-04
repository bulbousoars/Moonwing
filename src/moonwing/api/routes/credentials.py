from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from moonwing.api.deps import get_db
from moonwing.db.models import Credential
from moonwing.services.crypto import CryptoError, decrypt_api_key, encrypt_api_key, mask_api_key

router = APIRouter()


class CredentialCreate(BaseModel):
    owner_user_id: str | None = None
    scope: str = 'user'
    provider: str
    display_name: str
    api_key: str | None = None
    secret_ref: str | None = None  # legacy, ignored when api_key is set


@router.get('')
def list_credentials(db: Session = Depends(get_db)):
    creds = db.query(Credential).order_by(Credential.created_at.desc()).all()
    return [
        {
            'id': str(c.id),
            'scope': c.scope,
            'provider': c.provider,
            'display_name': c.display_name,
            'owner_user_id': str(c.owner_user_id) if c.owner_user_id else None,
            'created_at': c.created_at.isoformat() if c.created_at else None,
        }
        for c in creds
    ]


@router.get('/{credential_id}')
def get_credential(credential_id: UUID, db: Session = Depends(get_db)):
    cred = db.query(Credential).filter(Credential.id == credential_id).first()
    if not cred:
        raise HTTPException(status_code=404, detail='Credential not found')
    masked = None
    if cred.encrypted_api_key:
        try:
            masked = mask_api_key(decrypt_api_key(cred.encrypted_api_key))
        except CryptoError:
            masked = "(decryption failed)"
    return {
        'id': str(cred.id),
        'scope': cred.scope,
        'provider': cred.provider,
        'display_name': cred.display_name,
        'has_api_key': cred.encrypted_api_key is not None,
        'masked_key': masked,
        'owner_user_id': str(cred.owner_user_id) if cred.owner_user_id else None,
        'created_at': cred.created_at.isoformat() if cred.created_at else None,
    }


@router.post('', status_code=201)
def create_credential(payload: CredentialCreate, db: Session = Depends(get_db)):
    encrypted = None
    masked = "(no key)"
    if payload.api_key:
        encrypted = encrypt_api_key(payload.api_key)
        masked = mask_api_key(payload.api_key)

    cred = Credential(
        owner_user_id=UUID(payload.owner_user_id) if payload.owner_user_id else None,
        scope=payload.scope,
        provider=payload.provider,
        display_name=payload.display_name,
        secret_ref=payload.secret_ref or masked,
        encrypted_api_key=encrypted,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return {'id': str(cred.id), 'display_name': cred.display_name, 'has_api_key': encrypted is not None}
