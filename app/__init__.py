from flask import Flask, jsonify
from flask_cors import CORS
from config import Config

def create_app():
    app=Flask(__name__)
    app.config.from_object(Config)
    CORS(app, resources={r'/api/*': {'origins': app.config['FRONTEND_ORIGINS']}})
    @app.get('/api/health')
    def health():
        return jsonify(success=True, application='DSS API', status='online')
    return app
