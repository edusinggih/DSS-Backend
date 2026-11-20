import os
from dotenv import load_dotenv
load_dotenv()
class Config:
    SECRET_KEY=os.getenv('SECRET_KEY','CHANGE_ME')
    DB_HOST=os.getenv('DB_HOST','127.0.0.1')
    DB_PORT=int(os.getenv('DB_PORT','5432'))
    DB_NAME=os.getenv('DB_NAME','digital_suggestion_improvment')
    DB_USER=os.getenv('DB_USER','postgres')
    DB_PASS=os.getenv('DB_PASS','')
    FRONTEND_ORIGINS=[x.strip() for x in os.getenv('FRONTEND_ORIGINS','').split(',') if x.strip()]
