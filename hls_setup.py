import os
from pathlib import Path
import logging
import shutil
import subprocess

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def setup_hls_environment():
    """Setup HLS streaming environment with proper permissions"""
    try:
        # Define required directories
        directories = [
            '/tmp/hls_segments',
            '/tmp/hls_segments/temp',
            '/tmp/hls_segments/cache'
        ]

        # Create directories with proper permissions
        for dir_path in directories:
            path = Path(dir_path)
            if path.exists():
                logger.info(f"Cleaning existing directory: {dir_path}")
                shutil.rmtree(dir_path)
            
            logger.info(f"Creating directory: {dir_path}")
            path.mkdir(parents=True, exist_ok=True)
            
            # Set permissions (rwxrwxrwx)
            os.chmod(dir_path, 0o777)

        # Verify ffmpeg installation
        try:
            subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True)
            logger.info("ffmpeg is installed and accessible")
        except subprocess.CalledProcessError:
            logger.error("ffmpeg is not installed or not accessible")
            logger.info("Installing ffmpeg...")
            subprocess.run(['sudo', 'apt-get', 'update'], check=True)
            subprocess.run(['sudo', 'apt-get', 'install', '-y', 'ffmpeg'], check=True)

        # Test directory permissions by writing a test file
        test_file = Path('/tmp/hls_segments/test.txt')
        test_file.write_text('test')
        test_file.unlink()
        
        logger.info("HLS environment setup completed successfully")
        return True

    except Exception as e:
        logger.error(f"Error setting up HLS environment: {str(e)}")
        return False

if __name__ == "__main__":
    setup_hls_environment()