"""Run the public lab and a separate loopback-only human control listener."""
import os
import secrets
import signal
import subprocess
import sys

def main():
    def stop(signum,frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    processes=[]
    environment={**os.environ,'LAB_LOCAL_CONTROL_KEY':secrets.token_urlsafe(32)}
    try:
        for module,port in [('lab.local_control:app',8766),('lab.app:app',8765)]:
            processes.append(subprocess.Popen([sys.executable,'-m','uvicorn',module,'--host','127.0.0.1','--port',str(port),'--workers','1'],env=environment))
        while True:
            for process in processes:
                if process.poll() is not None: return process.returncode
            try:
                processes[-1].wait(timeout=.5)
            except subprocess.TimeoutExpired: pass
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None: process.terminate()
        for process in processes: process.wait()

if __name__=='__main__': sys.exit(main())
