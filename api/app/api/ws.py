from typing import List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import json
import logging

router = APIRouter()

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        # Filter out closed connections explicitly if needed, but remove handles it usually
        # We need to serialize to JSON
        text = json.dumps(message)
        for connection in self.active_connections:
            try:
                await connection.send_text(text)
            except:
                # If send fails, we might want to remove, but usually disconnect handles it
                pass

manager = ConnectionManager()

@router.websocket("/stream")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep the connection alive, maybe echo or just listen
            # We don't expect much input from client for now, mostly push
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
