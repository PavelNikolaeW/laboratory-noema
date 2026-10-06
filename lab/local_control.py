"""Loopback-only experimenter listener; never exposed through the participant API."""
import os
import secrets
from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .store import Store, uid

ORIGIN='http://127.0.0.1:8765'

def create_control(database=None,secret=None):
    app=FastAPI(docs_url=None,openapi_url=None,redoc_url=None)
    control_key=secret or os.getenv('LAB_LOCAL_CONTROL_KEY','')
    store=Store(database or os.getenv('LAB_DB','laboratory.db'))
    app.add_middleware(CORSMiddleware,allow_origins=[ORIGIN],allow_methods=['GET','POST'],allow_headers=['Authorization','Idempotency-Key'],allow_credentials=True)

    def error(code,message,status):
        return JSONResponse(status_code=status,content={'error':{'code':code,'message':message,'retryable':False},'request_id':uid()})

    @app.get('/capabilities')
    def capabilities(request:Request):
        return {'new_attempt':bool(control_key and secrets.compare_digest(request.cookies.get('lab-local-control',''),control_key))}

    @app.post('/attempts',status_code=201)
    async def new_attempt(request:Request,authorization:str|None=Header(None),key:str|None=Header(None,alias='Idempotency-Key')):
        if not control_key or not secrets.compare_digest(request.cookies.get('lab-local-control',''),control_key) or request.headers.get('origin')!=ORIGIN:
            return error('forbidden','Доступ только из локального рабочего стола',403)
        if not authorization or not authorization.startswith('Bearer '):
            return error('unauthorized','Нужен токен текущей попытки',401)
        if not key or len(key)>200 or await request.body():
            return error('invalid_parameters','Нужен ключ новой попытки; тело запроса должно быть пустым',422)
        try:
            return store.new_attempt(authorization[7:],key)
        except PermissionError:
            return error('unauthorized','Токен не найден',401)

    return app

app=create_control()
