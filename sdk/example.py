"""LAB_URL=... LAB_TOKEN=... python -m sdk.example [operation-id-to-resume]"""
import asyncio
import json
import os
import sys
from .client import LabClient

async def main():
    async with LabClient(os.getenv('LAB_URL','http://127.0.0.1:8765'),os.environ['LAB_TOKEN']) as lab:
        if len(sys.argv)>1:
            print(json.dumps(await lab.wait(sys.argv[1]),ensure_ascii=False,indent=2))
        else:
            print(json.dumps(await lab.get('session'),ensure_ascii=False,indent=2))
            page=await lab.events()
            print(json.dumps(page,ensure_ascii=False,indent=2))

if __name__=='__main__': asyncio.run(main())
