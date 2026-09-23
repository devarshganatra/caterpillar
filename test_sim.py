import asyncio
from simulator.sim import load_scenario, MACHINES, run_machine_loop, settings
import random
from unittest.mock import patch, AsyncMock

async def test_sim():
    scenario = load_scenario("simulator/scenarios/demo.yaml")
    rng1 = random.Random(42)
    rng2 = random.Random(42)
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # Run sim loop 1 for 10 iterations (sleep patched to 0)
        with patch("asyncio.sleep", return_value=None):
            t1 = asyncio.create_task(run_machine_loop("EXC001", MACHINES["EXC001"], rng1, scenario, "s", "http://test"))
            await asyncio.sleep(0.1) # Let it run some iterations
            t1.cancel()
        
        calls1 = [call[1][0] for call in mock_post.call_args_list]
        mock_post.reset_mock()
        
        # Run sim loop 2
        with patch("asyncio.sleep", return_value=None):
            t2 = asyncio.create_task(run_machine_loop("EXC001", MACHINES["EXC001"], rng2, scenario, "s", "http://test"))
            await asyncio.sleep(0.1)
            t2.cancel()
            
        calls2 = [call[1][0] for call in mock_post.call_args_list]
        
        # Check reproducibility
        for c1, c2 in zip(calls1[:10], calls2[:10]):
            c1_json = c1['json'].copy()
            c2_json = c2['json'].copy()
            c1_json.pop('ts', None)
            c2_json.pop('ts', None)
            c1_json.pop('sig', None)
            c2_json.pop('sig', None)
            assert c1_json == c2_json, "Seed not reproducible!"
            
    print("Sim reproducible seed ✓")

asyncio.run(test_sim())
