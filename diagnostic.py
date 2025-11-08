import os
import time
import asyncio
import psutil
import aiofiles
from pathlib import Path
import numpy as np
import json
from datetime import datetime

class DiagnosticTest:
    def __init__(self, test_dir="/tmp/io_test"):
        self.test_dir = Path(test_dir)
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.results = []
        self.process = psutil.Process()
        
    async def monitor_resources(self, duration=1, interval=0.1):
        """Monitor system resources for specified duration"""
        start_time = time.time()
        measurements = []
        
        while time.time() - start_time < duration:
            io_counters = psutil.disk_io_counters()
            measurements.append({
                'timestamp': time.time() - start_time,
                'cpu_percent': psutil.cpu_percent(),
                'memory_percent': self.process.memory_percent(),
                'io_counters': {
                    'read_bytes': io_counters.read_bytes,
                    'write_bytes': io_counters.write_bytes,
                    'read_count': io_counters.read_count,
                    'write_count': io_counters.write_count,
                    'read_time': io_counters.read_time,
                    'write_time': io_counters.write_time
                },
                'memory_info': dict(psutil.virtual_memory()._asdict())
            })
            await asyncio.sleep(interval)
        
        return measurements

    async def write_test(self, num_files=5, file_size_mb=100, chunk_size_mb=8):
        """Test concurrent file writes"""
        print(f"\nStarting write test with {num_files} files, {file_size_mb}MB each")
        
        start_memory = psutil.virtual_memory().percent
        start_io = psutil.disk_io_counters()
        total_start = time.time()
        
        # Start resource monitoring
        monitor_task = asyncio.create_task(self.monitor_resources(duration=60))
        
        # Generate random data for chunks
        chunk_size = chunk_size_mb * 1024 * 1024
        chunk_data = os.urandom(chunk_size)
        
        async def write_file(file_number):
            file_path = self.test_dir / f"test_file_{file_number}_{time.time()}.dat"
            write_stats = {'chunks': [], 'total_time': 0, 'slow_writes': 0}
            file_start = time.time()
            
            try:
                async with aiofiles.open(file_path, 'wb') as f:
                    bytes_remaining = file_size_mb * 1024 * 1024
                    while bytes_remaining > 0:
                        chunk_start = time.time()
                        write_size = min(chunk_size, bytes_remaining)
                        await f.write(chunk_data[:write_size])
                        chunk_time = time.time() - chunk_start
                        
                        write_stats['chunks'].append({
                            'size': write_size,
                            'time': chunk_time,
                            'speed': write_size / chunk_time / 1024 / 1024 if chunk_time > 0 else 0
                        })
                        
                        if chunk_time > 0.1:  # Log slow writes
                            write_stats['slow_writes'] += 1
                            print(f"Slow write detected on file {file_number}: "
                                  f"{chunk_time:.3f}s for {write_size/1024/1024:.1f}MB")
                        
                        bytes_remaining -= write_size
                
                write_stats['total_time'] = time.time() - file_start
                write_stats['avg_speed'] = (file_size_mb / write_stats['total_time']
                                          if write_stats['total_time'] > 0 else 0)
                return file_path, write_stats
                
            except Exception as e:
                print(f"Error writing file {file_number}: {e}")
                return file_path, None

        # Create all write tasks
        tasks = [write_file(i) for i in range(num_files)]
        results = await asyncio.gather(*tasks)
        
        # Get monitoring data
        monitor_results = await monitor_task
        
        # Calculate overall statistics
        end_time = time.time()
        end_memory = psutil.virtual_memory().percent
        end_io = psutil.disk_io_counters()
        
        total_duration = end_time - total_start
        successful_writes = [stats for _, stats in results if stats]
        
        if successful_writes:
            avg_speed = sum(s['avg_speed'] for s in successful_writes) / len(successful_writes)
            total_slow_writes = sum(s['slow_writes'] for s in successful_writes)
        else:
            avg_speed = 0
            total_slow_writes = 0
        
        test_results = {
            'test_type': 'concurrent_write',
            'num_files': num_files,
            'file_size_mb': file_size_mb,
            'total_duration': total_duration,
            'avg_speed_mbps': avg_speed,
            'total_slow_writes': total_slow_writes,
            'memory_impact': end_memory - start_memory,
            'io_impact': {
                'read_bytes': end_io.read_bytes - start_io.read_bytes,
                'write_bytes': end_io.write_bytes - start_io.write_bytes,
                'read_time': end_io.read_time - start_io.read_time,
                'write_time': end_io.write_time - start_io.write_time
            },
            'monitoring_data': monitor_results
        }
        
        self.results.append(test_results)
        
        # Cleanup files
        for file_path, _ in results:
            try:
                file_path.unlink()
            except Exception as e:
                print(f"Error cleaning up {file_path}: {e}")
        
        return test_results

    async def memory_test(self, size_mb=1000):
        """Test memory allocation and performance"""
        print(f"\nStarting memory test with {size_mb}MB allocation")
        
        monitor_task = asyncio.create_task(self.monitor_resources(duration=30))
        timings = []
        
        try:
            # Test memory allocation in chunks
            chunk_size_mb = 100
            num_chunks = size_mb // chunk_size_mb
            arrays = []
            
            for i in range(num_chunks):
                chunk_start = time.time()
                arr = np.zeros(chunk_size_mb * 1024 * 1024 // 8, dtype=np.float64)
                allocation_time = time.time() - chunk_start
                timings.append(allocation_time)
                
                if allocation_time > 0.1:
                    print(f"Slow memory allocation: {allocation_time:.3f}s for chunk {i+1}/{num_chunks}")
                
                arrays.append(arr)
            
            # Test memory operations
            op_start = time.time()
            for arr in arrays:
                arr += 1
            op_time = time.time() - op_start
            
            monitor_results = await monitor_task
            
            result = {
                'test_type': 'memory',
                'allocation_size_mb': size_mb,
                'chunk_size_mb': chunk_size_mb,
                'allocation_times': timings,
                'operation_time': op_time,
                'avg_allocation_time': sum(timings) / len(timings),
                'max_allocation_time': max(timings),
                'monitoring_data': monitor_results
            }
            
            self.results.append(result)
            return result
            
        finally:
            arrays = None  # Clear memory
            
    async def run_full_diagnostic(self):
        """Run a full suite of diagnostic tests"""
        print("Starting full diagnostic suite...")
        
        # Get system information
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        system_info = {
            'cpu_count': psutil.cpu_count(),
            'cpu_freq': psutil.cpu_freq()._asdict() if psutil.cpu_freq() else None,
            'memory_total_gb': memory.total / (1024**3),
            'memory_available_gb': memory.available / (1024**3),
            'disk_total_gb': disk.total / (1024**3),
            'disk_free_gb': disk.free / (1024**3),
            'timestamp': datetime.now().isoformat()
        }
        
        print("\nSystem Information:")
        for key, value in system_info.items():
            print(f"{key}: {value}")
        
        # Run write tests with increasing concurrency
        test_sizes = [5, 6, 7, 8]
        for size in test_sizes:
            try:
                result = await self.write_test(num_files=size, file_size_mb=100)
                print(f"\nResults for {size} concurrent files:")
                print(f"Average write speed: {result['avg_speed_mbps']:.2f} MB/s")
                print(f"Total duration: {result['total_duration']:.2f}s")
                print(f"Memory impact: {result['memory_impact']:.1f}%")
                print(f"Slow writes: {result['total_slow_writes']}")
            except Exception as e:
                print(f"Error during {size} file test: {e}")
        
        # Run memory test
        try:
            memory_result = await self.memory_test(size_mb=1000)
            print("\nMemory test results:")
            print(f"Average allocation time: {memory_result['avg_allocation_time']:.3f}s")
            print(f"Operation time: {memory_result['operation_time']:.3f}s")
        except Exception as e:
            print(f"Error during memory test: {e}")
        
        # Save complete results
        try:
            output_file = Path('diagnostic_results.json')
            full_results = {
                'system_info': system_info,
                'test_results': self.results
            }
            
            with open(output_file, 'w') as f:
                json.dump(full_results, f, indent=2)
            
            print(f"\nComplete diagnostic results saved to {output_file}")
            
        except Exception as e:
            print(f"Error saving results: {e}")

async def main():
    test = DiagnosticTest()
    await test.run_full_diagnostic()

if __name__ == "__main__":
    asyncio.run(main())