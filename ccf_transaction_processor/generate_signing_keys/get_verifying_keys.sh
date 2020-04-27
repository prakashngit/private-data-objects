#!/bin/bash

# Copyright 2020 Intel Corporation
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

# Usage ./get_verifying_keys.sh <ip-address-of-CCF>

# Copy KEYs for test
cp ../CCF/build/workspace/pdo_tp_common/user1_privk.pem .
cp ../CCF/build/workspace/pdo_tp_common/user1_cert.pem .
cp ../CCF/build/workspace/pdo_tp_common/networkcert.pem .

# Copy the infra folder
cp -r ../CCF/tests/infra infra

# activate the env
source ../CCF/build/env/bin/activate

echo "start ping test : 100 pings"
python key_read.py --host $1


