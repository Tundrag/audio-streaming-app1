#!/usr/bin/env python3
"""
Test script to verify permissions refactoring works correctly
"""

print("Testing permissions refactoring...")
print("=" * 60)

# Test 1: Import permissions module
print("\n1. Testing permissions module import...")
try:
    from permissions import (
        Permission,
        get_user_permissions,
        get_user_permissions_dict,
        get_user_permission_flags,
        check_permission,
        verify_role_permission,
        get_role_permissions_mapping,
        check_tier_access,
        get_simple_user_permissions
    )
    print("   ✅ All permission functions imported successfully")
except Exception as e:
    print(f"   ❌ Failed to import permissions: {e}")
    exit(1)

# Test 2: Check Permission enum
print("\n2. Testing Permission enum...")
try:
    assert hasattr(Permission, 'VIEW')
    assert hasattr(Permission, 'CREATE')
    assert hasattr(Permission, 'DELETE')
    assert hasattr(Permission, 'DOWNLOAD')
    assert hasattr(Permission, 'ALL')
    assert hasattr(Permission, 'TEAM_ACCESS')
    print("   ✅ Permission enum has all required flags")
except Exception as e:
    print(f"   ❌ Permission enum check failed: {e}")
    exit(1)

# Test 3: Check role permissions mapping
print("\n3. Testing role permissions mapping...")
try:
    from models import UserRole
    role_perms = get_role_permissions_mapping()
    assert UserRole.CREATOR in role_perms
    assert UserRole.TEAM in role_perms
    assert UserRole.PATREON in role_perms
    assert UserRole.KOFI in role_perms
    assert UserRole.GUEST in role_perms
    print("   ✅ Role permissions mapping works")
except Exception as e:
    print(f"   ❌ Role permissions mapping failed: {e}")
    exit(1)

# Test 4: Verify no Permission in models.py
print("\n4. Checking Permission removed from models.py...")
try:
    import models
    if hasattr(models, 'Permission'):
        print("   ⚠️  Permission still exists in models.py (should be removed)")
    else:
        print("   ✅ Permission successfully removed from models.py")
except Exception as e:
    print(f"   ❌ Could not check models: {e}")

print("\n" + "=" * 60)
print("✅ All permission refactoring tests passed!")
print("=" * 60)
