import os
from google.cloud.spanner import Client, PingingPool
import threading
from dotenv import load_dotenv

# Load the instance & database names specified in the .env file
load_dotenv()
INSTANCE_NAME = os.getenv('INSTANCE_NAME')
DATABASE_NAME = os.getenv('DATABASE_NAME')

client = Client()
instance = client.instance(INSTANCE_NAME)
'''
setup a PingingPool with only one session (size=1) & a ping_interval of 5 minutes
Note: 'ping_interval' does NOT represent the time after which the sessions are pinged
  'PingingPool' does not automatically ping its sessions. We need to manually call 'pool.ping()' to initiate a ping
  after which the 'PingingPool' decides whether to actually send a server side ping
  to the sessions or not based on whether a locally tracked timestamp of **most recent activity** on
  the session is older than the 'ping_interval' or not (emphasis on most recent activity, rather than last ping time)
'''
pool = PingingPool(size=1, default_timeout=5, ping_interval=300)
# initialize the database with the session pool
database = instance.database(DATABASE_NAME, pool=pool)

'''
Setup pinging of the sessions in the background to keep activity on the session to prevent spanner
  from deleting the session due to 1+ hour of inactivity
This background pinging does not protect from the issue described (server-side session deletion for reasons other than inactivity, more specifically
  sessions that are 28+ days old)
This is because 'PingingPool' keeps track of the 'latest_activity_time + ping_interval' (named 'ping_after' internally) for each sessions to
  decide whether it should ping & possibly recreate a new session if the session was deleted for any reason
The pitfall is that 'last_activity_time' keeps getting updated each time a session is requested from the pool
  So even if our query fails due to the session being deleted server-side, the 'ping_after' will be pushed forward and the session
  will not be pinged or checked for existence
'''
def background_loop():
    while True:
        pool.ping()

background = threading.Thread(target=background_loop, name='ping-pool')
background.daemon = True
background.start()


def execute_query():
    '''Execute an arbitrary query on your database
    Will refresh the locally tracked latest activity timestamp of the session within the PingingPool even if the query failed
      due to SessionNotFound error
    '''
    def run(transaction):
        query = "SELECT 1"
        transaction.execute_update(query)
    try:
        database.run_in_transaction(run)
        print("\nQuery executed successfuly\n")
    except Exception as e:
        print("\nQuery Failed\n")
        print(e)
        print("\n")

def delete_session():
    '''Delete the session from the server-side without removing it from the pool
    '''
    session = pool.get()
    pool.put(session)
    session.delete()

    assert session.exists() == False

    print(f"\nsession with id {session._session_id} successfully deleted\n")

def ping_pool():
    '''Execute a ping on the pool
    Will not fix the issue because the PingingPool conditionally checks whether the session should be actually pinged or not
      based on a locally tracked timestamp of latest activity
    '''
    pool.ping()
    print(f"\nPool pinged successfully\n")

if __name__ == '__main__':
    while True:
        print("What do you want to do?")
        print("1-Execute query")
        print("2-Delete session server side")
        print("3-Desperately try to ping the pool yourself\n")
        i = int(input())
        if i == 1:
            execute_query()
        elif i == 2:
            delete_session()
        elif i == 3:
            ping_pool()
        else:
            print("please enter 1 or 2 or 3\n")