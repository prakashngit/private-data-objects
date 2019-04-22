#!/bin/bash

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

PY3_VERSION=$(python --version | sed 's/Python 3\.\([0-9]\).*/\1/')
if [[ $PY3_VERSION -lt 5 ]]; then
    echo activate python3 first
    exit
fi

F_USAGE='-c|--count services --cfile config -l|--loglevel [debug|info|warn]'
F_LOGLEVEL='info'
F_COUNT=1
F_OUTPUTDIR=''
F_BASENAME='sservice'

F_SERVICEHOME="$( cd -P "$( dirname ${BASH_SOURCE[0]} )/.." && pwd )"
F_LOGDIR=$F_SERVICEHOME/logs
F_CONFDIR=$F_SERVICEHOME/etc

# -----------------------------------------------------------------
# Process command line arguments
# -----------------------------------------------------------------
TEMP=`getopt -o b:c:l:o: --long base:,count:,help,loglevel:,output: \
     -n 'es-start.sh' -- "$@"`

if [ $? != 0 ] ; then echo "Terminating..." >&2 ; exit 1 ; fi

eval set -- "$TEMP"
while true ; do
    case "$1" in
        -b|--base) F_BASENAME="$2" ; shift 2 ;;
        -c|--count) F_COUNT="$2" ; shift 2 ;;
        -l|--loglevel) F_LOGLEVEL="$2" ; shift 2 ;;
        -o|--output) F_OUTPUTDIR="$2" ; shift 2 ;;
        --help) echo $F_USAGE ; exit 1 ;;
	--) shift ; break ;;
	*) echo "Internal error!" ; exit 1 ;;
    esac
done

if [ "$F_OUTPUTDIR" != "" ] ; then
    mkdir -p $F_OUTPUTDIR
    rm -f $F_OUTPUTDIR/*
else
    EFILE=/dev/null
    OFILE=/dev/null
fi

for index in `seq 1 $F_COUNT` ; do
    IDENTITY="${F_BASENAME}$index"
    echo start storage service $IDENTITY

    rm -f $F_LOGDIR/$IDENTITY.log

    if [ "$F_OUTPUTDIR" != "" ]  ; then
        EFILE="$F_OUTPUTDIR/$IDENTITY.err"
        OFILE="$F_OUTPUTDIR/$IDENTITY.out"
        rm -f $EFILE $OFILE
    fi

    sservice --identity ${IDENTITY} --config ${IDENTITY}.toml --config-dir ${F_CONFDIR} \
             --loglevel ${F_LOGLEVEL} --logfile ${F_LOGDIR}/${IDENTITY}.log 2> $EFILE > $OFILE &

done