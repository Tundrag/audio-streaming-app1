# init_alembic.py
import os
from alembic.config import Config
from alembic import command
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_alembic_fresh():
    """Initialize Alembic from scratch"""
    try:
        # Remove existing alembic directory if it exists
        if os.path.exists('alembic'):
            import shutil
            shutil.rmtree('alembic')
            logger.info("Removed existing alembic directory")
        
        # Create new alembic.ini
        alembic_cfg = Config()
        alembic_cfg.set_main_option('script_location', 'alembic')
        alembic_cfg.set_main_option('sqlalchemy.url', 
            os.getenv('DATABASE_URL', 'postgresql://tundrag:Tundrag2010!@localhost/audio_streaming_db'))
        
        # Initialize alembic
        command.init(alembic_cfg, 'alembic')
        logger.info("Initialized fresh Alembic configuration")
        
        # Update env.py
        env_py_path = os.path.join('alembic', 'env.py')
        with open(env_py_path, 'r') as f:
            content = f.read()
        
        # Add import for your models
        content = content.replace(
            'from alembic import context',
            'from alembic import context\nfrom app import Base'
        )
        
        # Update target_metadata
        content = content.replace(
            'target_metadata = None',
            'target_metadata = Base.metadata'
        )
        
        with open(env_py_path, 'w') as f:
            f.write(content)
        logger.info("Updated env.py configuration")
        
        # Create versions directory
        os.makedirs(os.path.join('alembic', 'versions'), exist_ok=True)
        logger.info("Created versions directory")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to initialize Alembic: {str(e)}")
        return False

if __name__ == "__main__":
    init_alembic_fresh()