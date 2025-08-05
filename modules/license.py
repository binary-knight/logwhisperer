#!/usr/bin/env python3
"""
License validation module for LogWhisperer
Integrates with Gumroad's license verification API
"""

import os
import json
import time
import hashlib
import platform
import uuid
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import logging

logger = logging.getLogger(__name__)

# Configuration
LICENSE_CACHE_DIR = Path.home() / ".logwhisperer"
LICENSE_CACHE_FILE = LICENSE_CACHE_DIR / ".license"
MACHINE_ID_FILE = LICENSE_CACHE_DIR / ".machine_id"
GUMROAD_VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"
CACHE_DURATION_DAYS = 7  # Re-verify every 7 days
OFFLINE_GRACE_PERIOD_DAYS = 30  # Allow offline usage for 30 days

# Your Gumroad product ID (replace with your actual product ID)
GUMROAD_PRODUCT_ID = "your_product_id_here"


class LicenseError(Exception):
    """Base exception for license-related errors"""
    pass


class InvalidLicenseError(LicenseError):
    """Raised when license is invalid"""
    pass


class ExpiredLicenseError(LicenseError):
    """Raised when license has expired"""
    pass


class NetworkError(LicenseError):
    """Raised when network verification fails"""
    pass


def get_machine_id() -> str:
    """Generate a unique machine ID for license binding"""
    # Check if we already have a machine ID
    if MACHINE_ID_FILE.exists():
        return MACHINE_ID_FILE.read_text().strip()
    
    machine_data = {
        'platform': platform.platform(),
        'processor': platform.processor(),
        'node': platform.node(),
    }
    
    # Try to get MAC address without external dependencies
    try:
        mac = ':'.join(['{:02x}'.format((uuid.getnode() >> elements) & 0xff) 
                       for elements in range(0,8*6,8)][::-1])
        machine_data['mac'] = mac
    except:
        pass
    
    # Add more identifiers if available
    try:
        # Linux-specific
        if platform.system() == 'Linux':
            # Try to get machine-id
            for path in ['/etc/machine-id', '/var/lib/dbus/machine-id']:
                if Path(path).exists():
                    machine_data['system_id'] = Path(path).read_text().strip()
                    break
    except:
        pass
    
    # Generate hash
    machine_str = json.dumps(machine_data, sort_keys=True)
    machine_id = hashlib.sha256(machine_str.encode()).hexdigest()[:32]
    
    # Save for future use
    LICENSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MACHINE_ID_FILE.write_text(machine_id)
    
    return machine_id

def encrypt_license_data(data: Dict[str, Any], key: str) -> str:
    """Encrypt license data for local storage"""
    # Derive encryption key from license key
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b'logwhisperer_salt',  # In production, use a random salt
        iterations=100000,
    )
    key_bytes = base64.urlsafe_b64encode(kdf.derive(key.encode()))
    f = Fernet(key_bytes)
    
    # Encrypt data
    json_data = json.dumps(data).encode()
    encrypted = f.encrypt(json_data)
    return base64.urlsafe_b64encode(encrypted).decode()


def decrypt_license_data(encrypted_data: str, key: str) -> Dict[str, Any]:
    """Decrypt license data from local storage"""
    try:
        # Derive decryption key
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'logwhisperer_salt',
            iterations=100000,
        )
        key_bytes = base64.urlsafe_b64encode(kdf.derive(key.encode()))
        f = Fernet(key_bytes)
        
        # Decrypt data
        encrypted = base64.urlsafe_b64decode(encrypted_data.encode())
        decrypted = f.decrypt(encrypted)
        return json.loads(decrypted.decode())
    except Exception as e:
        logger.error(f"Failed to decrypt license data: {e}")
        raise InvalidLicenseError("License data corrupted")


def verify_with_gumroad(license_key: str, increment_uses: bool = True) -> Dict[str, Any]:
    """Verify license with Gumroad API"""
    try:
        response = requests.post(
            GUMROAD_VERIFY_URL,
            data={
                'product_id': GUMROAD_PRODUCT_ID,
                'license_key': license_key,
                'increment_uses_count': str(increment_uses).lower()
            },
            timeout=10
        )
        
        data = response.json()
        
        if not data.get('success'):
            error_msg = data.get('message', 'Invalid license')
            raise InvalidLicenseError(error_msg)
        
        purchase = data.get('purchase', {})
        
        # Check if license is refunded or chargebacked
        if purchase.get('refunded') or purchase.get('chargebacked'):
            raise InvalidLicenseError("License has been refunded or chargebacked")
        
        # Extract relevant data
        license_data = {
            'email': purchase.get('email'),
            'purchase_date': purchase.get('created_at'),
            'uses': data.get('uses', 0),
            'subscription': purchase.get('subscription_id') is not None,
            'product_name': purchase.get('product_name'),
            'verified_at': datetime.utcnow().isoformat(),
            'machine_id': get_machine_id(),
            'purchase': purchase  # Include full purchase data for custom fields
        }
        
        return license_data
        
    except requests.RequestException as e:
        logger.error(f"Network error verifying license: {e}")
        raise NetworkError("Failed to connect to license server")
    except Exception as e:
        logger.error(f"Error verifying license: {e}")
        raise


def save_license_cache(license_key: str, license_data: Dict[str, Any]) -> None:
    """Save encrypted license data to cache"""
    try:
        LICENSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
        cache_data = {
            'license_key': license_key,
            'data': license_data,
            'cached_at': datetime.utcnow().isoformat()
        }
        
        encrypted = encrypt_license_data(cache_data, license_key)
        LICENSE_CACHE_FILE.write_text(encrypted)
        
        # Set restrictive permissions
        os.chmod(LICENSE_CACHE_FILE, 0o600)
        
    except Exception as e:
        logger.error(f"Failed to save license cache: {e}")


def activate_license(license_key: str) -> Tuple[bool, str]:
    """Activate license for this machine"""
    machine_id = get_machine_id()
    
    # Check if already activated on this machine
    cached_data = load_license_cache(license_key)
    if cached_data and cached_data.get('machine_id') == machine_id:
        return True, "License already activated on this machine"
    
    # Verify with Gumroad and increment uses
    try:
        license_data = verify_with_gumroad(license_key, increment_uses=True)
        
        # Store activation
        license_data['machine_id'] = machine_id
        license_data['activated_at'] = datetime.utcnow().isoformat()
        
        save_license_cache(license_key, license_data)
        
        uses = license_data.get('uses', 1)
        return True, f"License activated (machine {uses} of 3)"
        
    except InvalidLicenseError as e:
        return False, str(e)

def deactivate_license(license_key: str) -> Tuple[bool, str]:
    """Deactivate license on this machine"""
    try:
        # This would need a Gumroad API endpoint or your own server
        # For now, just clear local cache
        if LICENSE_CACHE_FILE.exists():
            LICENSE_CACHE_FILE.unlink()
        
        return True, "License deactivated. You can now activate on another machine."
    except Exception as e:
        return False, f"Deactivation failed: {e}"

def load_license_cache(license_key: str) -> Optional[Dict[str, Any]]:
    """Load and validate cached license data"""
    if not LICENSE_CACHE_FILE.exists():
        return None
    
    try:
        encrypted = LICENSE_CACHE_FILE.read_text()
        cache_data = decrypt_license_data(encrypted, license_key)
        
        # Validate cache
        if cache_data.get('license_key') != license_key:
            logger.warning("License key mismatch in cache")
            return None
        
        # Check cache age
        cached_at = datetime.fromisoformat(cache_data['cached_at'])
        age = datetime.utcnow() - cached_at
        
        if age > timedelta(days=CACHE_DURATION_DAYS):
            logger.info("License cache expired")
            return None
        
        # Check machine ID
        if cache_data['data'].get('machine_id') != get_machine_id():
            logger.warning("Machine ID mismatch")
            return None
        
        return cache_data['data']
        
    except Exception as e:
        logger.error(f"Failed to load license cache: {e}")
        return None


def validate_license(license_key: Optional[str] = None, online_check: bool = True) -> Tuple[bool, str]:
    """
    Validate license key with enhanced security
    
    Returns:
        Tuple of (is_valid, message)
    """
    # Get license key from environment if not provided
    if not license_key:
        license_key = os.environ.get('LOGWHISPERER_LICENSE_KEY')
    
    if not license_key:
        return False, "No license key provided. Set LOGWHISPERER_LICENSE_KEY environment variable."
    
    # Clean the license key
    license_key = license_key.strip()
    
    # Get current machine ID
    current_machine_id = get_machine_id()
    
    # Try to load from cache first
    cached_data = load_license_cache(license_key)
    
    # Check if cache is valid for this machine
    if cached_data:
        cached_machine_id = cached_data.get('machine_id')
        if cached_machine_id != current_machine_id:
            logger.warning("License cache is for different machine")
            # Clear invalid cache
            if LICENSE_CACHE_FILE.exists():
                LICENSE_CACHE_FILE.unlink()
            cached_data = None
    
    # Check if online verification is required (every 7 days)
    force_online_check = False
    if cached_data:
        last_online_check = cached_data.get('last_online_check')
        if last_online_check:
            try:
                last_check_date = datetime.fromisoformat(last_online_check)
                days_since_check = (datetime.utcnow() - last_check_date).days
                if days_since_check >= 7:  # Require online check every 7 days
                    logger.info(f"Online verification required (last check: {days_since_check} days ago)")
                    force_online_check = True
            except:
                force_online_check = True
        else:
            force_online_check = True
    
    # Try online verification
    if online_check or force_online_check:
        try:
            # Always increment uses on new machine activation
            increment_uses = not cached_data or cached_data.get('machine_id') != current_machine_id
            
            license_data = verify_with_gumroad(license_key, increment_uses=increment_uses)
            
            # Check if this machine is already bound
            if cached_data and cached_data.get('machine_id') == current_machine_id:
                # Same machine - just update the cache
                license_data['machine_id'] = current_machine_id
                license_data['last_online_check'] = datetime.utcnow().isoformat()
            else:
                # New machine activation
                uses = license_data.get('uses', 1)
                max_uses = 3  # Default machine limit
                
                # Check custom fields from Gumroad for max_uses
                purchase = license_data.get('purchase', {})
                custom_fields = purchase.get('custom_fields', {})
                if 'max_uses' in custom_fields:
                    try:
                        max_uses = int(custom_fields['max_uses'])
                    except:
                        pass
                
                if uses > max_uses:
                    raise InvalidLicenseError(
                        f"License already activated on maximum number of machines ({max_uses}). "
                        f"Please deactivate on another machine first."
                    )
                
                license_data['machine_id'] = current_machine_id
                license_data['activation_count'] = uses
                license_data['max_activations'] = max_uses
                license_data['last_online_check'] = datetime.utcnow().isoformat()
            
            save_license_cache(license_key, license_data)
            
            email = license_data.get('email', 'Unknown')
            uses = license_data.get('activation_count', 1)
            max_uses = license_data.get('max_activations', 3)
            
            return True, f"License verified for: {email} (Machine {uses}/{max_uses})"
            
        except NetworkError:
            # Network error - fall back to cache if available
            if cached_data:
                # Check offline grace period
                verified_at = datetime.fromisoformat(cached_data.get('verified_at', cached_data.get('last_online_check', '2000-01-01')))
                offline_time = datetime.utcnow() - verified_at
                
                # If we're forced to check online but can't, enforce stricter limits
                if force_online_check:
                    max_offline_days = 14  # Stricter limit when check is required
                else:
                    max_offline_days = OFFLINE_GRACE_PERIOD_DAYS
                
                if offline_time <= timedelta(days=max_offline_days):
                    remaining_days = max_offline_days - offline_time.days
                    logger.warning(f"Running in offline mode. {remaining_days} days remaining.")
                    email = cached_data.get('email', 'Unknown')
                    uses = cached_data.get('activation_count', '?')
                    max_uses = cached_data.get('max_activations', '?')
                    return True, f"Offline mode - {email} (Machine {uses}/{max_uses}, {remaining_days} days remaining)"
                else:
                    return False, "Offline grace period expired. Please connect to internet to verify license."
            else:
                return False, "Cannot verify license. Please check your internet connection."
                
        except InvalidLicenseError as e:
            # Clear cache on invalid license
            if LICENSE_CACHE_FILE.exists():
                LICENSE_CACHE_FILE.unlink()
            return False, str(e)
        except Exception as e:
            logger.error(f"Unexpected error during license validation: {e}")
            return False, "License validation failed"
    
    # Offline check with cache
    if cached_data:
        # Verify machine ID matches
        if cached_data.get('machine_id') != current_machine_id:
            return False, "License is not valid for this machine"
        
        email = cached_data.get('email', 'Unknown')
        uses = cached_data.get('activation_count', '?')
        max_uses = cached_data.get('max_activations', '?')
        return True, f"Licensed to: {email} (Machine {uses}/{max_uses}, cached)"
    
    return False, "No valid license found"


def require_license(func):
    """Decorator to require valid license for function execution"""
    def wrapper(*args, **kwargs):
        is_valid, message = validate_license()
        if not is_valid:
            raise LicenseError(message)
        return func(*args, **kwargs)
    return wrapper


def check_license_cli():
    """CLI command to check license status"""
    print("LogWhisperer License Check")
    print("-" * 50)
    
    license_key = os.environ.get('LOGWHISPERER_LICENSE_KEY')
    if not license_key:
        license_key = input("Enter license key: ").strip()
    
    print("\nChecking license...")
    is_valid, message = validate_license(license_key)
    
    if is_valid:
        print(f"✓ License valid: {message}")
        
        # Show additional info if cached
        cached_data = load_license_cache(license_key)
        if cached_data:
            print(f"\nLicense Details:")
            print(f"  Product: {cached_data.get('product_name', 'Unknown')}")
            print(f"  Purchase Date: {cached_data.get('purchase_date', 'Unknown')}")
            print(f"  Machine ID: {get_machine_id()}")
            
            # Check cache age
            verified_at = datetime.fromisoformat(cached_data['verified_at'])
            age = datetime.utcnow() - verified_at
            print(f"  Last Verified: {age.days} days ago")
    else:
        print(f"✗ License invalid: {message}")
        return 1
    
    return 0


if __name__ == "__main__":
    # Test license check
    import sys
    sys.exit(check_license_cli())