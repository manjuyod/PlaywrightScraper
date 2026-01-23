"""
For running external jobs. 
Just the scraper for now
"""
from dataclasses import dataclass, field, asdict
from concurrent.futures import ThreadPoolExecutor, Future
import threading
from threading import Event, Lock
import asyncio
from scraper.runner import main as grade_checker
from scraper.work_flows.insert_grades import insert_grades
from queue import Queue
@dataclass
class JobState:
    # structure for a scraper task's progress i.e. update student
    total: int
    steps: int # the number of steps must be defined and will be used to determine completion
    step: int = -1
    pct: float = 0.0
    cancel: Event = field(default_factory=Event)
    lock: Lock = field(default_factory=Lock)
    
    def next_step(self):
        with self.lock:
            self.step += 1
            if self.step > self.steps: 
                self.step = -1
    
executor = ThreadPoolExecutor(max_workers=10)

jobs: dict[str, JobState] = {}
runners: dict[str, Future] = {}
state_q: Queue[tuple[str, JobState]] = Queue() 
jobs_lock = threading.Lock()
def state_consumer():
    while True:
        job_id, new_state = state_q.get()
        with jobs_lock:
            jobs[job_id] = new_state
            
# Long-running state consumer for jobs
threading.Thread(target=state_consumer, daemon=True).start()
def run_coro(coro):
    return asyncio.run(coro)
def start_grade_fetch_job(job_id: str, total: int) -> str:
    print(f"Starting scraper for job {job_id}")
    # franchise_id
    NONGOAL_STEPS = 3
    total_steps = total + NONGOAL_STEPS
    print(total_steps, "steps total", total, "students")
    jobs[job_id] = JobState(total=total, steps=total_steps)
    jobs[job_id].next_step()
    fut = executor.submit(
        run_coro, 
        grade_checker(franchise_id=franchise_from_job_id(job_id), student_id=student_from_job_id(job_id), job_id=job_id, state_q=state_q)
    )
    runners[job_id] = fut
    
    def _cleanup(_f: Future): # grade fetch done
        print("Runner cancelled?", runners.get(job_id, None).cancelled())
        print("Runner errored?", runners.get(job_id, None).exception())
        runners.pop(job_id, None)
        insert_grades()
        jobs[job_id].next_step()
        print(f"Scraper for job {job_id} done. {jobs[job_id].step} / {jobs[job_id].steps} steps completed.")

    fut.add_done_callback(_cleanup)
    return job_id

def get_status(job_id: str) -> JobState | None:
    with jobs_lock:
        state = jobs.get(job_id, None)
        if state is None:
            return None
        state.pct = state.step / state.steps
        return state
def is_running(job_id: str) -> bool:
    return job_id in runners.keys()

def franchise_from_job_id(job_id: str) -> int:
    return int(job_id.split('_')[0])
def student_from_job_id(job_id: str) -> int | None:
    parts = job_id.split('_')
    if len(parts) == 1:
        return None
    return int(parts[1])