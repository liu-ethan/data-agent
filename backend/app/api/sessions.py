from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.agent.memory.store import (
    MemoryError,
    create_session,
    delete_session,
    list_sessions,
    list_turns,
)
from app.auth.deps import get_current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
def get_sessions(user: Annotated[dict, Depends(get_current_user)]):
    return {"sessions": list_sessions(user["id"])}


@router.post("")
def post_session(user: Annotated[dict, Depends(get_current_user)]):
    return create_session(user["id"])


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_session(
    session_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    try:
        delete_session(session_id, user["id"])
    except MemoryError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{session_id}/turns")
def get_turns(session_id: str, user: Annotated[dict, Depends(get_current_user)]):
    try:
        turns = list_turns(
            session_id,
            user["id"],
            user_role=str(user.get("role") or "analyst"),
            hydrate=True,
        )
    except MemoryError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        ) from None
    return {"session_id": session_id, "turns": turns}
