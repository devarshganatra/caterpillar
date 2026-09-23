from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends, status
import asyncio
import logging
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from jose import JWTError, jwt

from backend.app.config import settings
from backend.app.db.session import get_db
from backend.app.db.models import User, Machine
from backend.app.api.deps import verify_site_access

logger = logging.getLogger(__name__)
router = APIRouter()

@router.websocket("/stream/{machine_id}")
async def websocket_endpoint(
    websocket: WebSocket, 
    machine_id: str, 
    ticket: str = Query(...),
    db: AsyncSession = Depends(get_db)
):
    await websocket.accept()
    
    try:
        # Validate ticket
        payload = jwt.decode(ticket, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "ws_ticket":
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid ticket type")
            return
            
        user_id = payload.get("sub")
        
        # Verify user exists
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalars().first()
        if not user or not user.is_active:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized user")
            return
            
        # Verify machine exists and find its site
        machine_result = await db.execute(select(Machine).where(Machine.id == machine_id))
        machine = machine_result.scalars().first()
        if not machine:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Machine not found")
            return
            
        # Verify site access
        has_access = await verify_site_access(machine.site_id, user, db)
        if not has_access:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized site access")
            return
            
    except JWTError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid ticket")
        return
        
    logger.info(f"User {user.id} authorized for WS on machine {machine_id}")
    
    redis_client = Redis.from_url(settings.redis_url)
    pubsub = redis_client.pubsub()
    
    channel = f"ui:{machine_id}"
    await pubsub.subscribe(channel)
    
    try:
        while True:
            # We must also handle disconnects from the client side,
            # so we wait for either a message from Redis or a client disconnect.
            
            message_task = asyncio.create_task(pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0))
            receive_task = asyncio.create_task(websocket.receive_text())
            
            done, pending = await asyncio.wait(
                [message_task, receive_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            if receive_task in done:
                # Client sent a message or disconnected
                try:
                    data = receive_task.result()
                    # We can handle ping/pong here if needed
                except WebSocketDisconnect:
                    logger.info(f"WebSocket client disconnected from {channel}")
                    break
                    
            if message_task in done:
                message = message_task.result()
                if message and message['type'] == 'message':
                    await websocket.send_text(message['data'].decode('utf-8'))
                    
            # Cancel pending tasks to prevent leaks
            for task in pending:
                task.cancel()
                
    except Exception as e:
        logger.error(f"WebSocket error on {channel}: {e}")
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await redis_client.close()
