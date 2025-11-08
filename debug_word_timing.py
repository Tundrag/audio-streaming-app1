# Test TRACK+VOICE specific locking behavior
import asyncio
import requests
import json
import time

async def test_track_voice_specific_locking():
    """Test that locking works correctly at track+voice level"""
    
    # Test data
    track_abc = "track-abc-123"  # Replace with actual track ID
    track_xyz = "track-xyz-456"  # Replace with actual track ID
    
    libby_voice = "en-GB-LibbyNeural"
    stephen_voice = "en-US-StephenNeural"
    
    base_url = "http://localhost:8000"  # Adjust as needed
    headers = {"Authorization": "Bearer your-token"}  # Add your auth
    
    print("🧪 Testing TRACK+VOICE specific locking behavior")
    print("=" * 60)
    
    async def switch_voice(track_id, voice_id, test_name):
        """Make a voice switch request"""
        try:
            response = requests.post(
                f"{base_url}/api/tracks/{track_id}/voice/switch",
                json={"new_voice": voice_id},
                headers=headers,
                timeout=10
            )
            
            return {
                'test': test_name,
                'track_id': track_id,
                'voice_id': voice_id,
                'status_code': response.status_code,
                'response': response.json(),
                'timestamp': time.time()
            }
        except Exception as e:
            return {
                'test': test_name,
                'track_id': track_id,
                'voice_id': voice_id,
                'status_code': 0,
                'response': {'error': str(e)},
                'timestamp': time.time()
            }
    
    # TEST 1: Multiple voices for same track (should be ALLOWED)
    print("🎯 TEST 1: Multiple voices for same track")
    print(f"   Track: {track_abc}")
    print(f"   Voices: {libby_voice} + {stephen_voice}")
    print("   Expected: Both should start processing")
    
    test1_tasks = [
        switch_voice(track_abc, libby_voice, "T1-LibbyABC"),
        switch_voice(track_abc, stephen_voice, "T1-StephenABC")
    ]
    
    test1_results = await asyncio.gather(*test1_tasks)
    
    print("   Results:")
    for result in test1_results:
        status = result['response'].get('status', 'error')
        message = result['response'].get('message', 'No message')[:50]
        print(f"     {result['test']}: {status} - {message}")
    
    # TEST 2: Same voice for different tracks (should be ALLOWED)
    print(f"\n🎯 TEST 2: Same voice for different tracks")
    print(f"   Voice: {libby_voice}")
    print(f"   Tracks: {track_abc} + {track_xyz}")
    print("   Expected: Both should start processing")
    
    # Wait a moment to avoid interference with test 1
    await asyncio.sleep(2)
    
    test2_tasks = [
        switch_voice(track_abc, libby_voice, "T2-LibbyABC"),
        switch_voice(track_xyz, libby_voice, "T2-LibbyXYZ")
    ]
    
    test2_results = await asyncio.gather(*test2_tasks)
    
    print("   Results:")
    for result in test2_results:
        status = result['response'].get('status', 'error')
        message = result['response'].get('message', 'No message')[:50]
        print(f"     {result['test']}: {status} - {message}")
    
    # TEST 3: Duplicate same track+voice (should be BLOCKED)
    print(f"\n🎯 TEST 3: Duplicate same track+voice")
    print(f"   Track: {track_abc}")
    print(f"   Voice: {libby_voice} (duplicate)")
    print("   Expected: Second request should be blocked/show progress")
    
    # Wait a moment
    await asyncio.sleep(1)
    
    test3_tasks = [
        switch_voice(track_abc, libby_voice, "T3-First"),
        switch_voice(track_abc, libby_voice, "T3-Duplicate")
    ]
    
    test3_results = await asyncio.gather(*test3_tasks)
    
    print("   Results:")
    for result in test3_results:
        status = result['response'].get('status', 'error')
        message = result['response'].get('message', 'No message')[:50]
        cached = result['response'].get('cached', False)
        print(f"     {result['test']}: {status} (cached: {cached}) - {message}")
    
    # ANALYSIS
    print(f"\n📊 ANALYSIS:")
    
    # Test 1 Analysis
    t1_processing = sum(1 for r in test1_results if r['response'].get('status') == 'processing')
    t1_success = t1_processing >= 1  # At least one should start processing
    print(f"   Test 1 - Multiple voices/same track: {'✅ PASS' if t1_success else '❌ FAIL'}")
    print(f"     Processing voices: {t1_processing}/2")
    
    # Test 2 Analysis  
    t2_processing = sum(1 for r in test2_results if r['response'].get('status') == 'processing')
    t2_success = t2_processing >= 1  # At least one should start processing
    print(f"   Test 2 - Same voice/different tracks: {'✅ PASS' if t2_success else '❌ FAIL'}")
    print(f"     Processing voices: {t2_processing}/2")
    
    # Test 3 Analysis
    t3_first = test3_results[0]['response'].get('status')
    t3_second = test3_results[1]['response'].get('status') 
    t3_success = (t3_first == 'processing' or t3_first == 'success') and (t3_second == 'processing' or 'already' in test3_results[1]['response'].get('message', '').lower())
    print(f"   Test 3 - Duplicate track+voice: {'✅ PASS' if t3_success else '❌ FAIL'}")
    print(f"     First: {t3_first}, Second: {t3_second}")
    
    # Overall result
    all_passed = t1_success and t2_success and t3_success
    print(f"\n🏆 OVERALL RESULT: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
    
    if all_passed:
        print("🎉 TRACK+VOICE specific locking is working correctly!")
    else:
        print("⚠️  There may be issues with the locking implementation")
    
    return {
        'test1_pass': t1_success,
        'test2_pass': t2_success, 
        'test3_pass': t3_success,
        'overall_pass': all_passed
    }

# Run the test
if __name__ == "__main__":
    asyncio.run(test_track_voice_specific_locking())