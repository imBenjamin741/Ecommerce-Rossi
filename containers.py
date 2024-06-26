# Requirements:
#   - docker
#   - python-dotenv
#   - pyyaml

import os
import sys
from dotenv import load_dotenv
import docker
import yaml
import tarfile
from io import BytesIO
import time
import subprocess
import glob

if not os.path.isfile('.env') or not os.path.isfile('compose.yaml'):
    exit()

with open('compose.yaml', 'r') as file:
    docker_compose = yaml.safe_load(file)

docker_client = docker.from_env()

load_dotenv()
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
DB_NAME = os.getenv('DB_NAME')

db_container_name = 'db'
django_container_name = 'backend'

def dump_db(dump_filename='db.dump'):
    db_container = get_container(db_container_name) 

    print('Dumping db...', end='\r')
    dump_result = db_container.exec_run(f'pg_dump -Fc -U {DB_USER} {DB_NAME}')

    if dump_result[0] != 0:
        print('Error while dumping')
        return False

    print('Db dumped    ')
    with open(dump_filename, "wb+") as f:
        print(f'Writing dump to {dump_filename}...', end='\r')
        f.write(dump_result[1])
        print(f'Dump written to {dump_filename}   ')
    return True

def copy_to_container(container, artifact_file, path='/tmp'):
    with create_archive(artifact_file) as archive:
        container.put_archive(path=path, data=archive)

def create_archive(artifact_file):
    pw_tarstream = BytesIO()
    pw_tar = tarfile.TarFile(fileobj=pw_tarstream, mode='w')
    file_data = open(artifact_file, 'rb').read()
    tarinfo = tarfile.TarInfo(name=artifact_file)
    tarinfo.size = len(file_data)
    tarinfo.mtime = time.time()
    # tarinfo.mode = 0600
    pw_tar.addfile(tarinfo, BytesIO(file_data))
    pw_tar.close()
    pw_tarstream.seek(0)
    return pw_tarstream

def restore_db(dump_filename):
    if not os.path.isfile(dump_filename):
        print(f'File "{dump_filename}" doesn\'t exists!')
        return False
    
    db_container = get_container(db_container_name)
    stop_container(django_container_name)

    print('Copying file to container...', end='\r')
    copy_to_container(db_container, dump_filename)
    print('File copied to container    ')

    print('Restoring db...', end='\r')
    restore_result = db_container.exec_run(f'pg_restore --clean --create -U {DB_USER} -d postgres /tmp/{dump_filename}')
    if restore_result[0] == 0:
        print('Db restored    ')

    # We restart django immediately after
    start_container(django_container_name)

    if restore_result[0] != 0:
        print('Error while restoring')
        return False
    return True

def create_db():
    db_container = get_container(db_container_name) 

    print('Creating db...', end='\r')
    dump_result = db_container.exec_run(f'createdb -U {DB_USER} {DB_NAME}')
    if dump_result[0] != 0:
        print('Error while creating db')
        return False
    print('Db re-created ')

    return True

def reset_db():
    db_container = get_container(db_container_name) 
    stop_container(django_container_name)

    print('Dropping db...', end='\r')
    dump_result = db_container.exec_run(f'dropdb -U {DB_USER} {DB_NAME}')
    if dump_result[0] != 0:
        print('Error while dropping db')
    else:
        print('Db dropped     ')
    
    create_db()
    start_container(django_container_name)

    if dump_result[0] != 0:
        return False
    return True

def reset_django():
    delete_migrations()
    reset_db()
    django_migrate()

def get_container(container_name):
    if container_name not in docker_compose['services']:
        return False
    
    try:
        return docker_client.containers.get(container_name)
    except docker.errors.NotFound:
        return None

def is_running(container_name):
    container = get_container(container_name)
    if not container:
        return False
    
    if container.attrs["State"]["Status"] == 'running':
        return True
    else:
        return False
    
def are_containers_running():
    for container in docker_compose['services']:
        if not is_running(container):
            return False
    return True

def start_container(container_name):
    container = get_container(container_name)
    
    if not container:
        print(f'Impossible to start "{container_name}" container: container not found')
        return False

    # Before overwriting the database is necessary to disconnect the django backend from it
    print(f'Starting "{container_name}" container...', end='\r')
    container.start()
    print(f'"{container_name}" container started    ')
    return True

def stop_container(container_name):
    container = get_container(container_name)
    
    if not container:
        print(f'Impossible to stop "{container_name}" container: container not found')
        return False

    # Before overwriting the database is necessary to disconnect the django backend from it
    print(f'Stopping "{container_name}" container...', end='\r')
    container.stop()
    print(f'"{container_name}" container stopped    ')
    return True

def delete_migrations():
    files = [x for x in glob.glob("backend/*/migrations/*.py") if '__init__.py' not in x]
    files = files + glob.glob("backend/*/migrations/*.pyc")
    
    for file in files:
        os.remove(file)

def django_migrate():
    subprocess.run(f'docker exec -it backend /app/manage.py makemigrations', shell=True)
    subprocess.run(f'docker exec -it backend /app/manage.py migrate', shell=True)
    
def manage(args=''):
    subprocess.run(f'docker exec -it backend /app/manage.py {args}', shell=True)

def compose_build():
    subprocess.run('docker compose build', shell=True)

def compose_up():
    subprocess.run('docker compose up -d', shell=True)

def shell(container_name):
    subprocess.run(f'docker exec -it {container_name} sh', shell=True)

def compose_down():
    subprocess.run('docker compose down', shell=True)
    
def print_usage():
    print('Usage: python containers.py [action]')
    print()
    print('Actions:')
    print('-h   --help                      Print help')
    print('build [?up]                      Build and eventually run containers')
    print('up                               Run containers')
    print('down                             Stop containers')
    print("shell [container_name]           Enter container's shell")
    print('dump [?filename="db.dump"]       Dump database')
    print('restore [filename]               Restore database from dump')
    print('resetdb                          Drop and re-create db')
    print('resetdjango                      Reset db and django migrations')
    print('manage [options]                 Run django manage.py')


def main():
    containers_running = are_containers_running()
    
    delete_migrations()

    if len(sys.argv) == 1:
        print_usage()
    elif sys.argv[1] == '-h' or sys.argv[1] == '--help' or sys.argv[1] == 'help':
        print_usage()
    elif sys.argv[1] == 'build':
        compose_build()
        if len(sys.argv) > 2 and sys.argv[2] == 'up':
            compose_up()
    elif sys.argv[1] == 'up':
        compose_up()
    elif not containers_running:
        print(f'Run "python containers.py up" before running {sys.argv[1]}!')
    elif sys.argv[1] == 'shell':
        if len(sys.argv) < 3:
            print('Missing container name!')
        if len(sys.argv) > 3:
            print('Too many arguments!')
        shell(sys.argv[2])
    elif sys.argv[1] == 'down':
        compose_down()
    elif sys.argv[1] == 'dump':
        if len(sys.argv) > 2:
            dump_db(sys.argv[2])
        else:
            dump_db()
    elif sys.argv[1] == 'restore':
        if len(sys.argv) < 3:
            print('Missing filename of the dump to restore!')
        elif len(sys.argv) > 3:
            print('Too many arguments!')
        else:
            restore_db(sys.argv[2])
    elif sys.argv[1] == 'resetdb':
        reset_db()
    elif sys.argv[1] == 'resetdjango':
        reset_django()
    elif sys.argv[1] == 'manage':
        manage(' '.join(sys.argv[2:]))
    
    else:
        print(f'Command {sys.argv[1]} not found. Refer to the usage manual:')
        print_usage()



if __name__ == "__main__":
    main()

