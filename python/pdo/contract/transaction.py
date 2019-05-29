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

import pdo.common.crypto as crypto
from sawtooth.helpers.pdo_connect import PdoRegistryHelper
from pdo.submitter.submitter import Submitter

import logging
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------
__transaction_executor__ = concurrent.futures.ThreadPoolExecutor(max_workers=1) # executor that submit transactions
__pending_transactions_queue__ = queue.Queue()

__ids_of_completed_transactions__ = set()
__condition_variable_for_completed_transactions__ = threading.Condition() # used to notify the parent thread about a new task
                                                                          # that got completed (if the parent is waiting)

__ids_of_failed_transactions__ = set()

# -----------------------------------------------------------------
class Dependencies(object) :
    """
    Class for mapping contract state commits to the corresponding
    ledger transaction. This class facilitates efficient assignment
    of dependencies in PDO transactions.
    """

    ## -------------------------------------------------------
    def __init__(self) :
        self.__depcache = {}

    ## -------------------------------------------------------
    def __key(self, contractid, statehash) :
        return str(contractid) + '$' + str(statehash)

    ## -------------------------------------------------------
    def __set(self, contractid, statehash, txnid) :
        self.__depcache[self.__key(contractid, statehash)] = txnid

    ## -------------------------------------------------------
    def __get(self, contractid, statehash) :
        k = self.__key(contractid, statehash)
        return self.__depcache.get(k)

    ##--------------------------------------------------------
    def FindDependencyLocally(self, contractid, statehash):
        return self.__get(contractid, statehash)

    ## -------------------------------------------------------
    def FindDependency(self, ledger_config, contractid, statehash) :
        logger.debug('find dependency for %s, %s', contractid, statehash)

        txnid = self.__get(contractid, statehash)
        if txnid :
            return txnid

        # no information about this update locally, so go to the
        # ledger to retrieve it
        client = PdoRegistryHelper(ledger_config['LedgerURL'])

        try :
            # this is not very efficient since it pulls all of the state
            # down with the txnid
            contract_state_info = client.get_ccl_state_dict(contractid, statehash)
            txnid = contract_state_info['transaction_id']
            self.__set(contractid, statehash, txnid)
            return txnid
        except Exception as e :
            logger.info('failed to retrieve the transaction: %s', str(e))

        logger.info('unable to find dependency for %s:%s', contractid, statehash)
        return None

    ## -------------------------------------------------------
    def SaveDependency(self, contractid, statehash, txnid) :
        self.__set(contractid, statehash, txnid)

# -----------------------------------------------------------------
__transaction_dependencies__ = Dependencies()

# -----------------------------------------------------------------
def start_transaction_processing_service():

    __transaction_executor__.submit(__transaction_worker__)

# -----------------------------------------------------------------
def stop_transacion_processing_service():

    # send termination signal
    __pending_transactions_queue__.put(dict({'exit_now': True}))

    #shutdown executor
    __transaction_executor__.shutdown(wait=True)

# -----------------------------------------------------------------
def add_transaction_task(task):

    __pending_transactions_queue__.put(task)

# -----------------------------------------------------------------
def are_there_failed_transactions():

    return len(__ids_of_failed_transactions__) > 0

# -----------------------------------------------------------------
def wait_for_transaction_task(commit_id):

    release_lock = False
    while commit_id[2] not in __ids_of_completed_transactions__:
        if are_there_failed_transactions(): # this checks for failed transactions from past including those that are not dependencies.
                                            # Refine this later to only check for failures of commit_id and its (chain of) depedencies
                                            # alternatively, just check for commit_id with an explicit timeout
            raise Exception("Transaction submission failed for request number %d", commit_id[2])
        __condition_variable_for_completed_transactions__.acquire()
        __condition_variable_for_completed_transactions__.wait(timeout=1.0)
        release_lock = True

    if release_lock:
        __condition_variable_for_completed_transactions__.release()

    contract_id = commit_id[0]
    state_hash = commit_id[1]
    txn_id = __transaction_dependencies__.FindDependencyLocally(contract_id, crypto.byte_array_to_base64(state_hash))

    return txn_id

# -----------------------------------------------------------------
def __transaction_worker__():
    """This is the worker for submitting transactions"""

    def submit_doable_transactions_for_contract(contract_id):
        """ helper function to submit pending transactions for a specific contact.
        Transactions will be submitted for all pending commits whose commit dependecies are met"""

        nonlocal rep_completed_but_txn_not_submitted_updates

        submitted_any = False

        pending_requests_numbers = list(rep_completed_but_txn_not_submitted_updates[contract_id].keys())
        pending_requests_numbers.sort()
        for request_number in pending_requests_numbers:

            task = rep_completed_but_txn_not_submitted_updates[contract_id][request_number]
            response = task['response_object']
            commit_id = response.commit_id
            transaction_request = response.transaction_request
            txn_dependencies = []

            # check for commit dependencies (due to commits arising from the same client)
            if transaction_request.dependency_list_commit_ids is not None:
                
                # Check for implicit dependency: (check to ensure that the transaction corresponding to the old_state_hash 
                # was committed (by the same client)). Don't have to add them to txn_dependencies, this will be added by submitter
                if response.operation != 'initialize' and request_number > 0:
                    txnid = __transaction_dependencies__.FindDependencyLocally(contract_id, crypto.byte_array_to_base64(response.old_state_hash))
                    if txnid is None:
                        break

                # check for explicit dependencies: (specfied by the client during the commit call)
                fail_explit_commit_dependencies = False
                for commit_id_temp in transaction_request.dependency_list_commit_ids:

                    # check if the transaction for commit_id_temp failed, if so we will not submit transaction for this request ever
                    if commit_id_temp[2] in __ids_of_failed_transactions__:
                        logger.info("Aborting transaction for request %d since one or more dependencies have failed", request_number)
                        __ids_of_failed_transactions__.add(request_number)
                        del rep_completed_but_txn_not_submitted_updates[contract_id][request_number] # remove the task from the pending list
                        fail_explit_commit_dependencies = True
                        break

                    txnid = __transaction_dependencies__.FindDependencyLocally(commit_id_temp[0], crypto.byte_array_to_base64(commit_id_temp[1]))
                    if txnid :
                        txn_dependencies.append(txnid)
                    else:
                        fail_explit_commit_dependencies = True
                        break

                if fail_explit_commit_dependencies:
                    break

            # OK, all commit dependencies are met. Add any transaction dependecies explicitly specified by client durind the commit call.
            # (transactions can come from other clients). These will be checked by the submitter
            for txn_id in transaction_request.dependency_list_txnids:
                txn_dependencies.append(txn_id)

            # submit txn
            try:
                if response.operation != 'initialize' :
                    txn_id =  __submit_update_transaction__(response, transaction_request.ledger_config, wait=transaction_request.wait, \
                        transaction_dependency_list=txn_dependencies)
                else:
                    txn_id = __submit_initialize_transaction__(response, transaction_request.ledger_config, wait=transaction_request.wait)

                del rep_completed_but_txn_not_submitted_updates[contract_id][request_number] # remove the task from the pending list

                if txn_id:
                    logger.info("Submitted transaction for request number %d", request_number)
                    submitted_any = True
                    # add the commit_id to completed list, notify any waiting thread
                    __ids_of_completed_transactions__.add(request_number)
                    __condition_variable_for_completed_transactions__.acquire()
                    __condition_variable_for_completed_transactions__.notify()
                    __condition_variable_for_completed_transactions__.release()
                else:
                    logger.error("Did not get a transaction id after transaction submission,  request nunmber %d", request_number)
                    __ids_of_failed_transactions__.add(request_number)
                    break
            except Exception as e:
                logger.error("Transaction submission failed for request number %d: %s", request_number, str(e))
                __ids_of_failed_transactions__.add(request_number)
                break

        return submitted_any

    # -------------------------------------------------------
    rep_completed_but_txn_not_submitted_updates = dict() # key is contract_id, value is dict(k:v). k = request_number from the commit _id
    # and v is everything else needed to submit transaction

    while True:
        task = __pending_transactions_queue__.get()
        # check if the task corresponds to a termination signal (indicating an exception of any kind)
        if task.get('exit_now'):
            logger.info("Exiting transaction submission woker thread")
            break
        commit_id = task['response_object'].commit_id
        contract_id = commit_id[0]
        request_number = commit_id[2]

        if rep_completed_but_txn_not_submitted_updates.get(contract_id):
            rep_completed_but_txn_not_submitted_updates[contract_id][request_number] =  task
        else:
            rep_completed_but_txn_not_submitted_updates[contract_id] = dict({request_number: task})

        # submit transactions as many as possible for the contract_id just added
        submitted_any = submit_doable_transactions_for_contract(contract_id)

        # loop over all contracts_ids. For each check contract_id, submit transactions as many as possible.
        # Continue looping until no transaction can be submitted for any conrtract_id
        if submitted_any and len(rep_completed_but_txn_not_submitted_updates.keys()) > 1:
            loop_again = True
            while loop_again:
                loop_again = False
                for contract_id in rep_completed_but_txn_not_submitted_updates.keys():
                    loop_again = loop_again or submit_doable_transactions_for_contract(contract_id)

# -------------------------------------------------------
def __submit_initialize_transaction__(response, ledger_config, **extra_params):
    """submit the initialize transaction to the ledger
    """

    if response.status is False :
        raise Exception('attempt to submit failed initialization transactions')

    # an initialize operation has no previous state
    assert not response.old_state_hash

    initialize_submitter = Submitter(
        ledger_config['LedgerURL'],
        key_str = response.channel_keys.txn_private)

    b64_message_hash = crypto.byte_array_to_base64(response.message_hash)
    b64_new_state_hash = crypto.byte_array_to_base64(response.new_state_hash)
    b64_code_hash = crypto.byte_array_to_base64(response.code_hash)

    raw_state = response.raw_state
    try :
        raw_state = raw_state.decode()
    except AttributeError :
        pass

    txnid = initialize_submitter.submit_ccl_initialize_from_data(
        response.originator_keys.signing_key,
        response.originator_keys.verifying_key,
        response.channel_keys.txn_public,
        response.enclave_service.enclave_id,
        response.signature,
        response.contract_id,
        b64_message_hash,
        b64_new_state_hash,
        raw_state,
        b64_code_hash,
        **extra_params)

    if txnid :
        __transaction_dependencies__.SaveDependency(response.contract_id, b64_new_state_hash, txnid)

    return txnid

# -------------------------------------------------------
def __submit_update_transaction__(response, ledger_config, **extra_params):
    """submit the update transaction to the ledger
    """

    if response.status is False :
        raise Exception('attempt to submit failed update transaction')

    # there must be a previous state hash if this is
    # an update
    assert response.old_state_hash

    update_submitter = Submitter(
        ledger_config['LedgerURL'],
        key_str = response.channel_keys.txn_private)

    b64_message_hash = crypto.byte_array_to_base64(response.message_hash)
    b64_new_state_hash = crypto.byte_array_to_base64(response.new_state_hash)
    b64_old_state_hash = crypto.byte_array_to_base64(response.old_state_hash)

    # convert contract dependencies into transaction dependencies
    # to ensure that the sawtooth validator does not attempt to
    # re-order the transactions since it is unaware of the semantics
    # of the contract dependencies
    txn_dependencies = set()
    if extra_params.get('transaction_dependency_list') :
        txn_dependencies.update(extra_params['transaction_dependency_list'])

    txnid = __transaction_dependencies__.FindDependency(ledger_config, response.contract_id, b64_old_state_hash)
    if txnid :
        txn_dependencies.add(txnid)

    for dependency in response.dependencies :
        contract_id = dependency['contract_id']
        state_hash = dependency['state_hash']
        txnid = __transaction_dependencies__.FindDependency(ledger_config, contract_id, state_hash)
        if txnid :
            txn_dependencies.add(txnid)
        else :
            raise Exception('failed to find dependency; {0}:{1}'.format(contract_id, state_hash))

    if txn_dependencies :
        extra_params['transaction_dependency_list'] = list(txn_dependencies)

    raw_state = response.raw_state
    try :
        raw_state = raw_state.decode()
    except AttributeError :
        pass

    # now send off the transaction to the ledger
    txnid = update_submitter.submit_ccl_update_from_data(
        response.originator_keys.verifying_key,
        response.channel_keys.txn_public,
        response.enclave_service.enclave_id,
        response.signature,
        response.contract_id,
        b64_message_hash,
        b64_new_state_hash,
        b64_old_state_hash,
        raw_state,
        response.dependencies,
        **extra_params)

    if txnid :
        __transaction_dependencies__.SaveDependency(response.contract_id, b64_new_state_hash, txnid)

    return txnid

# -----------------------------------------------------------------
class TransactionRequest(object):

    def __init__(self, ledger_config, wait = 30, dependency_list_txnids=[], dependency_list_commit_ids=[]):

        self.ledger_config = ledger_config
        self.wait = wait
        self.dependency_list_txnids = dependency_list_txnids
        self.dependency_list_commit_ids = dependency_list_commit_ids
