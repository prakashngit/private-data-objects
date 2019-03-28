# Copyright 2018 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import concurrent.futures
import queue
import threading

from pdo.contract.state import ContractState
import pdo.service_client.service_data.eservice as service_db

import logging
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------
__replication_manager_executor__ = concurrent.futures.ThreadPoolExecutor(max_workers=1)
__pending_replication_tasks_manager_queue__ = queue.Queue()

__replication_workers_executor__ = dict() #key is service id, value is a ThreadPoolExecutor object that manages the worker threads for this storage service
num_threads_per_storage_service = 2 # we many want to parametrize this later
__pending_replication_tasks_workers_queues__ = dict() #key is service id, value is a queue of pending replication tasks for this storage service

__num_successful_replicas_for_each_task__ = dict() # key is task (request) id, value is number of replicas that have been successfully completed yet.
__num_unsuccessful_replicas_for_each_task__ = dict() # key is task (request) id, value is number of replicas that have been unsuccssfully completed yet.
__lock_for_num_completed_replicas_for_each_task__ = threading.RLock()

__ids_of_completed_tasks__ = set()
__condition_variable_for_completed_tasks__ = threading.Condition() # used to notify the parent thread about a new task that got completed (if the parent is waiting)

__ids_of_failed_tasks__ = set()

__ids_of_services_in_use__ = set() #id is the verifying key corresponding to the service
__ids_of_services_to_ignore__ = set()

# -----------------------------------------------------------------
def start_replication_service():

    # start the manager
    __replication_manager_executor__.submit(__replication_manager__)

# -----------------------------------------------------------------
def stop_replication_service():

    # send termination signal to the replication executor threads
    __pending_replication_tasks_manager_queue__.put(dict({'exit_now': True}))

    #shutdown replication executor
    __replication_manager_executor__.shutdown(wait=True)

# -----------------------------------------------------------------
def add_replication_task(task):

    __pending_replication_tasks_manager_queue__.put(task)

# -----------------------------------------------------------------
def wait_for_replication_task(commit_id):

    release_lock = False
    while commit_id[2] not in __ids_of_completed_tasks__:
        if commit_id[2] in __ids_of_failed_tasks__:
            logger.info("Replication task failed for commit id %s", str(commit_id))
            raise Exception("Replication task failed for commit id %s", str(commit_id))
        __condition_variable_for_completed_tasks__.acquire()
        __condition_variable_for_completed_tasks__.wait(timeout = 1.0)
        release_lock = True

    if release_lock:
        __condition_variable_for_completed_tasks__.release()

# -----------------------------------------------------------------
def are_there_failed_replication_tasks():

    return len(__ids_of_failed_tasks__) > 0

# -----------------------------------------------------------------
def __set_up_worker__(service_id):

    __replication_workers_executor__[service_id] = concurrent.futures.ThreadPoolExecutor(max_workers=num_threads_per_storage_service)
    __pending_replication_tasks_workers_queues__[service_id] = queue.Queue()
    condition_variable_for_setup = threading.Condition()
    for i in range(num_threads_per_storage_service):
        __replication_workers_executor__[service_id].submit(__replication_worker__, service_id, __pending_replication_tasks_workers_queues__[service_id], \
            condition_variable_for_setup)
        #wait for the thread to initialize
        condition_variable_for_setup.acquire()
        condition_variable_for_setup.wait()
        condition_variable_for_setup.release()

# -----------------------------------------------------------------
def __shutdown_workers__():
    """Shutdown all worker threads"""

    # send terminal signals to the worker threads
    for service_id in __replication_workers_executor__.keys():
        for i in range(num_threads_per_storage_service):
            __pending_replication_tasks_workers_queues__[service_id].put(dict({'exit_now': True}))

    #shutdown the worker executors
    for service_id in __replication_workers_executor__.keys():
        __replication_workers_executor__[service_id].shutdown(wait=True)

# -----------------------------------------------------------------
def __update_set_of_completed_tasks__(request_id, call_back_after_replication):

    # add the request_id to set of completed tasks. We prevent multiple notifications for the same completed task
    __condition_variable_for_completed_tasks__.acquire()
    if request_id not in __ids_of_completed_tasks__:
        __ids_of_completed_tasks__.add(request_id)
        logger.info("Replication for request id %d successfully completed", request_id)
        call_back_after_replication()
        __condition_variable_for_completed_tasks__.notify()
    __condition_variable_for_completed_tasks__.release()

# -----------------------------------------------------------------
def __update_num_completed_replicas_for_each_task__(request_id, fail_task):

    __lock_for_num_completed_replicas_for_each_task__.acquire()

    if not __num_unsuccessful_replicas_for_each_task__.get(request_id):
        __num_unsuccessful_replicas_for_each_task__[request_id] = 0

    if not __num_successful_replicas_for_each_task__.get(request_id):
        __num_successful_replicas_for_each_task__[request_id] = 0

    if fail_task:
        __num_unsuccessful_replicas_for_each_task__[request_id]+=1
    else:
        __num_successful_replicas_for_each_task__[request_id]+=1

    __lock_for_num_completed_replicas_for_each_task__.release()

# -----------------------------------------------------------------
def __replication_manager__():
    """ Manager thread for a replication task"""

    while True:
        # get the task from the job queue
        task = __pending_replication_tasks_manager_queue__.get()

        # check for termination signal
        if task.get('exit_now'):
            __shutdown_workers__()
            logger.info("Exiting Replication manager thread")
            break

        replication_request = task['response_object'].replication_request
        request_id = task['response_object'].commit_id[2]

        # identify if replication can be skipped, including the case where there is only one provisioned enclave
        if len(replication_request.service_ids) == 1:
            logger.info('Skipping replication for request id %d : Only one provisioned enclave, so nothing to replicate', request_id)
            __update_set_of_completed_tasks__(request_id, task['response_object'].call_back_after_replication)
            continue

        if len(replication_request.blocks_to_replicate) == 0:
            logger.info('Skipping replication for request id %d: No change set, so nothing to replicate', request_id)
            __update_set_of_completed_tasks__(request_id, task['response_object'].call_back_after_replication)
            continue

        #ensure that the worker threads and queues are in place for services associated with this replication request
        for service_id in replication_request.service_ids:
            if __replication_workers_executor__.get(service_id) is None:
                __set_up_worker__(service_id)

        # get the set of services to use with this task
        ids_of_services_to_use = replication_request.service_ids - __ids_of_services_to_ignore__

        #check that there are enough services for replication, else add to the set of failed tasks and go to the next task
        if len(ids_of_services_to_use) < replication_request.num_provable_replicas:
            logger.error("Replication failed for request number %d. Not enough reliable storage services.", request_id)
            __ids_of_failed_tasks__.add(request_id)
            continue

        # add the task to the workers queues
        for service_id in ids_of_services_to_use:
            __pending_replication_tasks_workers_queues__[service_id].put(task)

# -----------------------------------------------------------------
def __replication_worker__(service_id, pending_taks_queue, condition_variable_for_setup):
    """ Worker thread that replicates to a specific storage service"""
    # set up the service client
    try:
        service_client = service_db.get_client_by_id(service_id)
        init_sucess = True
    except:
        logger.info("Failed to set up service client for service id %s", str(service_id))
        # mark the service as unusable
        __ids_of_services_to_ignore__.add(service_id)
        #exit the thread
        init_sucess = False

    # notify the manager that init was attempted
    condition_variable_for_setup.acquire()
    condition_variable_for_setup.notify()
    condition_variable_for_setup.release()

    # exit the thread if init failed
    if not init_sucess:
        return

    while True:
        # wait for a new task
        task = pending_taks_queue.get()

        # check for termination signal
        if task.get('exit_now'):
            logger.info("Exiting replication worker thread for service %s", str(service_client.ServiceURL))
            break

        request_id = task['response_object'].commit_id[2]

        #check if the task is already complete. If so go to the next one
        if request_id in __ids_of_completed_tasks__:
            continue

        # replicate now!
        replication_request = task['response_object'].replication_request
        block_data_list = ContractState.block_data_generator(replication_request.contract_id, replication_request.blocks_to_replicate, replication_request.data_dir)
        expiration = replication_request.availability_duration
        try:
            fail_task = False
            response = service_client.store_blocks(block_data_list, expiration)
            if response is None :
                fail_task =  True
                logger.info("No response from storage service %s for replication request %d", str(service_client.ServiceURL), request_id)
        except Exception as e:
            fail_task =  True
            logger.info("Replication request %d got an exception from %s: %s", request_id, str(service_client.ServiceURL), str(e))

        # update the count of successful/unsuccessful replicas for the task
        __update_num_completed_replicas_for_each_task__(request_id, fail_task)

        # check if the overall task can be marked as successful or failed:
        if __num_successful_replicas_for_each_task__[request_id] >= replication_request.num_provable_replicas:
            __update_set_of_completed_tasks__(request_id, task['response_object'].call_back_after_replication)
        elif __num_unsuccessful_replicas_for_each_task__[request_id] > len(replication_request.service_ids) - replication_request.num_provable_replicas:
            __ids_of_failed_tasks__.add(request_id)

        # Finally, if the task failed, mark the service as unreliable (this may be a bit harsh, we will refine this later based on the nature of the failure)
        if fail_task:
            __ids_of_services_to_ignore__.add(service_id)
            logger.info("Ignoring service at %s for rest of replication attempts", str(service_client.ServiceURL))
            #exit the thread
            break

# -----------------------------------------------------------------
class ReplicationRequest(object):
    """ implements the replicator class functionality : used for change-set replication after
    contract update, before commiting transaction."""

    # -----------------------------------------------------------------
    def __init__(self, replication_params, \
                contract_id, blocks_to_replicate, data_dir=None):
        """ Create an instance of the ReplicationRequest class: used for replicating the current change set.
        eservice_ids can be replaced with sservice_ids after we create an sservice database that can be used to look up the sservice url using the id."""

        self.service_ids = replication_params['service_ids']
        self.num_provable_replicas = replication_params['num_provable_replicas']
        self.availability_duration = replication_params['availability_duration']
        self.contract_id = contract_id
        self.blocks_to_replicate = blocks_to_replicate
        self.data_dir = data_dir
