from pydantic import BaseModel
from typing import List

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    patient_id: str
    messages: List[ChatMessage]

class ClinicalQuery(BaseModel):
    patient_id: str
    prompt: str