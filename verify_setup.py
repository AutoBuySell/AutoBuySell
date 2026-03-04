import sys
import os

# Add api directory to path
sys.path.append(os.path.abspath("api"))

try:
    print("Verifying Imports...")
    from app.core.config import settings
    print(f"✅ Config Loaded: {settings.PROJECT_NAME}")
    
    from app.core.database import Base
    print("✅ Database Models Loaded")
    
    from app.domain.models import Order, Position
    print("✅ Domain Models Verified")
    
    from app.brokers.alpaca import AlpacaBroker
    print("✅ Alpaca Broker Importable")
    
    from app.services.trading import TradingService
    print("✅ Trading Service Importable")
    
    print("\nAll Core Modules Validated Successfully!")
    
except Exception as e:
    print(f"\n❌ Verification Failed: {e}")
    sys.exit(1)
