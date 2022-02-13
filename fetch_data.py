"""
Usage: fetch_data (targets) <query> [--users | --tweets] [options]

Fetch a list of targets from Twitter API.
- In the users mode, <query> refers to usernames, and we get their friends and followers.
- In the tweets mode, <query> refers to a search query, and we get the users of the resulting tweets.

Options:
  -h --help                   Show this screen.
  --max-tweets-count <type>   Maximum number of tweets to fetch before stopping. [default: 250000].
  --graph-nodes <type>        Nodes to consider in the graph: friends, followers or all. [default: followers].
  --edges-ratio <ratio>       Ratio of edges to export in the graph (chosen randomly among non-mutuals). [default: 1].
  --credentials <file>        Path of the credentials for Twitter API [default: credentials.json].
  --excluded <file>           Path of the list of excluded users [default: excluded.json].
  --out <path>                Directory of output files [default: out].
  --run-http-server           Run an HTTP server to visualize the graph in you browser with d3.js.
  --save_frequency <type>     Number of account between each save in cache. [default: 100].
"""
from functools import partial
from time import sleep
import requests
import twitter
import json
import pandas as pd
import random
from docopt import docopt
from pathlib import Path



TWITTER_RATE_LIMIT_ERROR = 88


def fetch_users(apis, target, are_users, nodes_to_considere, max_tweets_count, out_path,
                followers_file="cache/followers.json",
                friends_file="cache/friends.json",
                tweets_file="cache/tweets.json"):
    """
        Fetch a list of users from Twitter API.

        - If a target (user) is provided, get their friends and followers.
        - Alternatively, if a search query is provided, get the resulting tweets and their users.
          These users are returned as "followers" of the query, and the list of friends is None.

        The tweets, friends and followers are all cached in json files.

    :param List[twitter.Api] apis: a list of Twitter API instances
    :param str target: screen-name of a target
    :param str are_users: true if the target is an user, false otherwise
    :param str nodes_to_considere: Nodes to consider in the graph: friends, followers or all.
    :param int max_tweets_count: maximum number of tweets fetched
    :param Path out_path: the path to the output directory
    :param str followers_file: the followers filename in the cache
    :param str friends_file: the friends filename in the cache
    :param str tweets_file: the tweets filename in the cache
    :return: followers, friends, intersection of both, and union of both
    """
    if not are_users:
        tweets = get_or_set(out_path / target / tweets_file,
                            partial(fetch_tweets, search_query=target, apis=apis, max_count=max_tweets_count),
                            api_function=True)
        print("Found {} tweets.".format(len(tweets)))
        followers = [{**tweet["user"], "query_created_at": tweet["created_at"]} for tweet in tweets]
        print("Found {} unique authors.".format(len(set(fol["id"] for fol in followers))))
        get_or_set(out_path / target / followers_file, followers, api_function=False)
        friends = []
    else:
        api_idx = 0
        next_cursor = -1
        followers = []
        friends = []

        if nodes_to_considere == "followers" or  nodes_to_considere == "all":
            while next_cursor != 0:
                try:
                    print("Using {} cursor.".format(next_cursor))
                    next_cursor, previous_cursor, new_followers, = apis[api_idx].GetFollowersPaged(screen_name=target, count=200, cursor=next_cursor)
                    followers += [user._json for user in new_followers]
                    print("Found {} followers.".format(len(followers)))
                except twitter.error.TwitterError as e:
                    if not isinstance(e.message, str) and e.message[0]["code"] == TWITTER_RATE_LIMIT_ERROR:
                        api_idx = (api_idx + 1) % len(apis)
                        print(f"You reached the rate limit. Moving to next api: #{api_idx}")
                        sleep(1)
                    else:
                        print("...but it failed. Error: {}".format(e))
            get_or_set(out_path / target / followers_file,  followers, force=True)

        next_cursor = -1
        if nodes_to_considere == "friends" or  nodes_to_considere == "all":
            while next_cursor != 0:
                try:
                    print("Using {} cursor.".format(next_cursor))
                    next_cursor, previous_cursor, new_friends, = apis[api_idx].GetFriendsPaged(screen_name=target, count=200, cursor=next_cursor)
                    friends += [user._json for user in new_friends]
                    print("Found {} friends.".format(len(friends)))
                except twitter.error.TwitterError as e:
                    if not isinstance(e.message, str) and e.message[0]["code"] == TWITTER_RATE_LIMIT_ERROR:
                        api_idx = (api_idx + 1) % len(apis)
                        print(f"You reached the rate limit. Moving to next api: #{api_idx}")
                        sleep(1)
                    else:
                        print("...but it failed. Error: {}".format(e))
            get_or_set(out_path / target / friends_file, friends, force=True)


    followers_ids = [user["id"] for user in followers]
    mutuals = [user["id"] for user in friends if user["id"] in followers_ids]
    all_users = followers + [user for user in friends if user["id"] not in followers_ids]
    return followers, friends, mutuals, all_users

def fetch_friendships(apis, users, excluded, out, target, save_frequency=100, friends_restricted_to=None, friendships_file="cache/friendships.json"):
    """
        Fetch the friends of a list of users from Twitter API
    :param List[twitter.Api] apis: a list of Twitter API instances
    :param list users: the users whose friends to look for
    :param list excluded: path to a file containing the screen names of users whose friends not to look for
    :param Path out: the path to output directory
    :param list friends_restricted_to: the set of potential friends to consider
    :param friendships_file: the friendships filename in the cache
    """
    friendships = get_or_set(out / target / friendships_file, {})
    friends_restricted_to = friends_restricted_to if friends_restricted_to else users
    users_ids = set([str(user["id"]) for user in friends_restricted_to])
    excluded = get_or_set(excluded, [])
    api_idx = 0
    for i, user in enumerate(users):
        if user["screen_name"] in excluded:
            continue
        if str(user["id"]) in friendships:
            print(f"[{len(friendships)}] @{user['screen_name']} found in cache.")
        else:
            print(f"[{len(friendships)}] Fetching friends of @{user['screen_name']}")
            user_friends = []
            stuck = 0
            while not user_friends:
                if stuck == 150:
                    break
                try:
                    next_cursor = -1
                    previous_cursor = 0
                    while previous_cursor != next_cursor and next_cursor != 0:
                        next_cursor, previous_cursor, new_user_friends,  = apis[api_idx].GetFriendIDsPaged(user_id=user["id"], stringify_ids=True, cursor=next_cursor)
                        user_friends = user_friends + new_user_friends
                        if not user_friends:
                            user_friends = [""]
                except twitter.error.TwitterError as e:
                    if not isinstance(e.message, str) and e.message[0]["code"] == TWITTER_RATE_LIMIT_ERROR:
                        api_idx = (api_idx + 1) % len(apis)
                        print(f"You reached the rate limit. Moving to next api: #{api_idx}")
                        sleep(13)
                        stuck += 1
                    else:
                        print("...but it failed. Error: {}".format(e))
                        user_friends = [""]

            common_friends = set(user_friends).intersection(users_ids)
            friendships[str(user["id"])] = list(common_friends)
            # Write to file
            if i % save_frequency == 0:
                get_or_set(out / target / friendships_file, friendships.copy(), force=True)
    get_or_set(out / target / friendships_file, friendships, force=True)
    return friendships


def fetch_tweets(search_query, apis, max_count=1000000):
    all_tweets, tweets, max_id = [], [], None
    max_id = 0
    api_idx = 0
    while len(all_tweets) < max_count:
        try:
            tweets = apis[api_idx].GetSearch(term=search_query,
                                   count=100,
                                   result_type="recent",
                                   #until="2022-01-16",
                                   #since_id=1465930821580341250,
                                   # since="2021-12-01",
                                   max_id=max_id)
        except twitter.error.TwitterError as e:
            if not isinstance(e.message, str) and e.message[0]["code"] == TWITTER_RATE_LIMIT_ERROR:
                api_idx = (api_idx + 1) % len(apis)
                print(f"You reached the rate limit. Moving to next api: #{api_idx}")
            else:
                print("...but it failed. Error: {}".format(e))
                user_friends = [""]

        all_tweets.extend(tweets)
        print(f"Found {len(all_tweets)}/{max_count} tweets.")
        if len(tweets) < 100:
            print("Done: no more tweets.")
            break
        max_id = min(tweet.id for tweet in tweets)
    print(f"First & last tweet dates are: {all_tweets[0].created_at} - {all_tweets[-1].created_at}")
    return all_tweets


# noinspection PyProtectedMember
def get_or_set(path, value=None, force=False, api_function=False):
    """
        Get a value from a file if it exists, else write the value to the file.
        The value can also be a API callback, in which case the call is made only when the file is written.
    :param Path path: file path
    :param value: the value to write to the file, if known
    :param bool force:  force writing the value to the file, even if it already exists
    :param bool api_function: if the value an API function? If yes, value must be a callback for the API call.
    :return: the got or written value
    """
    # Get
    if path.exists() and not force:
        with path.open("r") as f:
            value = json.load(f)
    # Set
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        if api_function:
            result = value()
            value = [item._json for item in result]
        with path.open("w") as f:
            json.dump(value, f, default=dumper, indent=2)
    return value

def dumper(obj):
    try:
        return obj.toJSON()
    except:
        return obj.__dict__

def save_to_graph(users, friendships, out_path, target, edges_ratio=1.0, protected_users=None):
    columns = [field for field in users[0] if field not in ["id", "id_str"]]
    nodes = {user["id_str"]: [user.get(field, "") for field in columns] for user in users}
    users_df = pd.DataFrame.from_dict(nodes, orient='index', columns=columns)
    users_df["Label"] = users_df["name"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    nodes_path = out_path / target / "nodes.csv"
    users_df.to_csv(nodes_path, index_label="Id")
    print("Successfully exported {} nodes to {}.".format(users_df.shape[0], nodes_path))
    users_ids = [user["id_str"] for user in users]

    if edges_ratio < 1:
        protected_users = [user["id_str"] for user in protected_users] if protected_users else []
        edges, protected_edges = [], []
        for source, source_friends in friendships.items():
            if source not in users_ids:
                continue
            if source in protected_users:
                protected_edges += [[source, target] for target in source_friends if target in users_ids]
            else:
                edges += [[source, target] for target in source_friends if target in users_ids]
        edges = random.choices(edges, k=int(edges_ratio * len(edges)))
        edges += protected_edges
    else:
        print("Start calculated edge")
        edges = [[source, target] for source, source_friends in friendships.items() if source in users_ids
                 for target in source_friends if target in users_ids]
        print("finish calculated edge")

    print("create datafram")
    edges_df = pd.DataFrame(edges, columns=['Source', 'Target'])
    edges_path = out_path / target / "edges.csv"
    print("to csv")
    edges_df.to_csv(edges_path)
    print("Successfully exported {} edges to {}.".format(edges_df.shape[0], edges_path))

    return nodes_path, edges_path

def main():
    options = docopt(__doc__)
    credentials = json.loads(open(options["--credentials"]).read())
    apis = [
        twitter.Api(consumer_key=credential["api_key"],
                    consumer_secret=credential["api_secret_key"],
                    access_token_key=credential["access_token"],
                    access_token_secret=credential["access_token_secret"],
                    sleep_on_rate_limit=False)
        for credential in credentials
    ]

    try:
        search_query = options["<query>"].split(',')
        are_users = True if options["--users"] else False
        nodes_to_considere = options["--graph-nodes"]
        for target in search_query:
            print("Process query {}".format(target))
            followers, friends, mutuals, all_users = fetch_users(apis, target, are_users, nodes_to_considere,
                                                                 int(options["--max-tweets-count"]),
                                                                 Path(options["--out"]))
            users = {"followers": followers, "friends": friends, "all": all_users,
                     "few": random.choices(followers, k=min(100, len(followers)))}[options["--graph-nodes"]]
            friendships = fetch_friendships(apis, users, Path(options["--excluded"]), Path(options["--out"]), target, int(options["--save_frequency"]), friends_restricted_to=all_users)
            save_to_graph(users, friendships, Path(options["--out"]), target, protected_users=mutuals)
    except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
        print(e)  # Why do I get these?
        main()  # Retry!


if __name__ == "__main__":
    main()
