"""Participant transport only: no engine imports or experimental strategies."""
import asyncio
import uuid
import httpx
from .types import OperationRequest, PublicOperation, EventPage

class LabAPIError(Exception):
    def __init__(self,status,payload):
        self.status,self.payload=status,payload
        super().__init__(payload.get('error',{}).get('message','API error'))

class LabClient:
    def __init__(self,base_url,token):
        self.http=httpx.AsyncClient(base_url=base_url.rstrip('/')+'/api/v1/',headers={'Authorization':'Bearer '+token},timeout=20)
    async def __aenter__(self): return self
    async def __aexit__(self,*args): await self.http.aclose()
    async def get(self,path,**params): return await self.request('GET',path,params=params)
    async def post(self,path,body,key=None):
        # A caller persisting a key can safely retry this request after a transport failure.
        return await self.request('POST',path,json=body,headers={'Idempotency-Key':key or uuid.uuid4().hex})
    async def request(self,method,path,**kwargs):
        response=await self.http.request(method,path,**kwargs)
        if response.is_error: raise LabAPIError(response.status_code,response.json())
        return response.json()
    async def wait(self,operation_id,timeout=60):
        async with asyncio.timeout(timeout):
            while True:
                result=await self.get('operations/'+operation_id)
                if result['status'] in ('succeeded','failed'): return result
                await asyncio.sleep(.2)
    async def operate(self,body:OperationRequest,key:str) -> PublicOperation:
        return await self.post('operations',body,key)
    async def events(self,after=0,limit=100) -> EventPage: return await self.get('events',after=after,limit=limit)
