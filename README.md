This script illustrates an issue with the [`PingingPool` class](https://github.com/googleapis/python-spanner/blob/d3fe937aa928a22e9ca43b601497d4a2555932fc/google/cloud/spanner_v1/pool.py#L350) of [`google-cloud-spanner` python client](https://github.com/googleapis/python-spanner)  

## Issue description:

When using a google cloud spanner session that has been deleted from server-side, you should get a `404 Session NotFound` error.

Currently google cloud spanner deletes sessions from the server side for the following reasons [source](https://cloud.google.com/knowledge/kb/unable-to-create-a-session-with-spanner-instance-000004362#:~:text=It%20seems%20like%20session%20was,client%20can%20delete%20a%20session.):
1) A client can delete a session.
2) The Cloud Spanner database service can delete a session when the session is idle for more than 1 hour.
3) The Cloud Spanner database service may delete a session if the session is more than 28 days old.

Using `PingingPool` mainly protects the library user from (2) but does not cover (1) & does not cover (3) in case there is a constant flow of requests to the pool.  
I'm mostly concerned with (3) though.

## Issue reason:

1) `PingingPool` keeps track of a locally calculated timestamp called `ping_after` associated with each session in the pool.
2) When `PingingPool` is pinged (using `ping()`). `PingingPool` uses the `ping_after` timestamp and compares it to the current time to check whether it should actually initiate a server ping (by sending a dummy query such as `SELECT 1`) or should just do nothing
3) the `ping_after` represents `most_recent_activity_on_the_session` + `ping_interval`. Where:
   * `ping_interval` is a supplied option in seconds
   * `most_recent_activity_on_the_session` is the last time the session was returned to the pool
  
I assume the rationale behind checking the `ping_after` is to not send unnecessary pings to the server when the session has been recently used so it is assumed to be fresh/active

## Case where this fails:

Assume you have a `PingingPool` of size 1. The session in this pool has been active for 28+ days, so spanner service decided to delete it from server side (`PingingPool` is unaware of the deletion).

Here is what happens when client attempts to use the deleted session

**Client:** Request a session from the pool  
**PingingPool:** Returns the deleted session (unaware that it was deleted since there are no checks on existence at get time)  
**Client:** Attempts to use the deleted session, receives `404 Session NotFound`, gracefully returns the session to the pool (Client doesn't differentiate between `Session NotFound` errors and any other errors)  
**PingingPool:** Puts the deleted session back into the pool, **refreshes the `ping_after` timestamp**, making future `ping` attempts useless since from the pool's POV, the session was recently used.  

All of that while our background thread desperately trying to ping the pool, but having no side-effect since the `ping_after` keeps getting refreshed by client requesting the dead session and putting it back in the pool.

## Victims of this issues:

Long runnning deployments using `PingingPool` where there is a constant flow of requests.  

Note: The mention of _constant flow of request_ is because if there hasn't been any requests to the `PingingPool` for the duration of the `ping_interval`, `PingingPool`'s ping will actually go through, and will replace deleted session with a new one

## Proposed solutions:

1- Have `PingingPool` keep track of session creation time, add an extra check to `ping` to make it go through if sessions are >= 28 days old  
   This will cover the case of spanner deleting old sessions, but will not cover the general case of server-side deletions.  
   However, it seems that the only other case is manual deletion of a session (who does that?). So if you're going to manually delete a session, might as well manually refresh your pool. Hence, this is my suggested solution  
   
2- Have `PingingPool` clients specially check for `Session NotFound` errors and replace the deleted session with a new session  
   This is implemented in the Java client library. Seems like a lot of effort though.  

## References:

This issue was referenced in other google cloud libraries and there are solutions to them

### PHP:

issue: https://github.com/googleapis/google-cloud-php/issues/5827  
Solution PR (implemented the tracking of session created time): https://github.com/googleapis/google-cloud-php/pull/5853

Another issue ~4 months after by the same user asking to handle more general cases of session deletion but was deemed too much effort: https://github.com/googleapis/google-cloud-php/issues/6284

### Go:

issue: https://github.com/googleapis/google-cloud-go/issues/1527  
Solution: They seemed to also go for the 2nd proposed solution, they did it gradually on multiple CLs mentioned in the referenced issues tho.  

### Java:

Couldn't find issue references, but here is a PR that solves the issue by the 2nd proposed solution: https://github.com/googleapis/google-cloud-java/pull/4734


## How to use this script to illustrate the issue

### Prerequisites:

1) Local installation of `poetry`
2) Spanner instance & database created.
3) Select the project in which the database is created to be the default project by executing `gcloud init` locally

### Setup
1) Clone the repo, cd into it, and create a `.env` file similar to the `.env.sample` and supply your instance & db names

2) Run the following to enter into poetry venv
```
poetry shell
```

3) Install the needed packages
```
poetry install
```

4) After installation is done run the script
```
python3 script.py
```

### Usage

You will be greeted by an interactive cli
```
What do you want to do?
1-Execute query
2-Delete session server side
3-Desperately try to ping the pool yourself
```

Enter `1` to test sending a dummy query to your database (the query is `SELECT 1`)

You should have this printed in your console
> Query executed successfuly

Now enter `2` to delete the session from server side while having the pool be unaware of the deletion

You should have this printed on your console with {session-id} replaced with whatever id of the session was
> session with id _{session-id}_ successfully deleted

Now enter `1` again to try to send another query.

You should get this response
> Query Failed  
404 Session not found: projects/_{project-id}_/instances/_{instance-name}_/databases/_{database-name}_/sessions/_{session-id}_ [resource_type: "type.googleapis.com/google.spanner.v1.Session"
resource_name: "projects/_{project-id}_/instances/_{instance-name}_/databases/_{session-name}_/sessions/_{session-id}_"
description: "Session does not exist."
]

Enter `3` to try to manually initiate a pool ping

You should see this
> Pool pinged successfully

Now enter `1` again to see how pinging the pool did not help

You should see the same error again
