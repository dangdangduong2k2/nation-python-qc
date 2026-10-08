"""Nation SDK calls and frames over a physical ZK serial reader."""
from .adapter import AutoReader, AutoUART, DetectionError

__version__ = '0.1.0a1'
__all__ = ['AutoReader', 'AutoUART', 'DetectionError']
