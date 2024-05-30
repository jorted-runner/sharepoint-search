from flask import Flask, redirect, render_template, request, session, url_for, copy_current_request_context, flash
from flask_session import Session
import identity.web
import requests

import os
import json
import threading

import app_config

__version__ = "0.8.0"  # The version of this sample, for troubleshooting purpose

app = Flask(__name__)
background_task_running = False
app.config.from_object(app_config)
assert app.config["REDIRECT_PATH"] != "/", "REDIRECT_PATH must not be /"
Session(app)

# This section is needed for url_for("foo", _external=True) to automatically
# generate http scheme when this sample is running on localhost,
# and to generate https scheme when it is deployed behind reversed proxy.
# See also https://flask.palletsprojects.com/en/2.2.x/deploying/proxy_fix/
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.jinja_env.globals.update(Auth=identity.web.Auth)  # Useful in template for B2C
auth = identity.web.Auth(
    session=session,
    authority=app.config["AUTHORITY"],
    client_id=app.config["CLIENT_ID"],
    client_credential=app.config["CLIENT_SECRET"],
)

@app.route("/login")
def login():
    return render_template("login.html", version=__version__, **auth.log_in(
        scopes=app_config.SCOPE, # Have user consent to scopes during log-in
        redirect_uri=url_for("auth_response", _external=True), # Optional. If present, this absolute URL must match your app's redirect_uri registered in Azure Portal
        prompt="select_account",  # Optional. More values defined in  https://openid.net/specs/openid-connect-core-1_0.html#AuthRequest
        ))


@app.route(app_config.REDIRECT_PATH)
def auth_response():
    result = auth.complete_log_in(request.args)
    if "error" in result:
        return render_template("auth_error.html", result=result)
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    return redirect(auth.log_out(url_for("index", _external=True)))


@app.route("/")
def index():
    # Start `download_contacts` in a new thread
    @copy_current_request_context
    def background_task():
        global background_task_running
        background_task_running = True
        try:
            download_contacts()
        finally:
            background_task_running = False
    global background_task_running
    if background_task_running:
        pass
    elif not os.path.exists('clients.json'):
        threading.Thread(target=background_task).start()

    if not (app.config["CLIENT_ID"] and app.config["CLIENT_SECRET"]):
        return render_template('config_error.html')
    if not auth.get_user():
        return redirect(url_for("login"))
    return render_template('index.html', user=auth.get_user(), version=__version__)

@app.route("/call_downstream_api")
def call_downstream_api():
    token = auth.get_token_for_user(app_config.SCOPE)
    if "error" in token:
        return redirect(url_for("login"))
    # Use access token to call downstream api
    api_result = requests.get(
        app_config.ENDPOINT,
        headers={'Authorization': 'Bearer ' + token['access_token']},
        timeout=30,
    ).json()
    return render_template('display.html', result=api_result)

def download_contacts():
    token = auth.get_token_for_user(scopes=['Sites.Read.All'])
    site_url = app_config.ORG_BASE_URL
    # Fetch site information
    site_response = requests.get(
        f'https://graph.microsoft.com/v1.0/sites/{site_url}',
        headers={'Authorization': f'Bearer {token["access_token"]}'},
        timeout=30
    )
    site_response.raise_for_status()
    site_info = site_response.json()
    site_id = site_info['id']

    # Fetch all lists in the site
    lists_response = requests.get(
        f'https://graph.microsoft.com/v1.0/sites/{site_id}/lists',
        headers={'Authorization': f'Bearer {token["access_token"]}'},
        timeout=30
    )
    lists_response.raise_for_status()
    team_lists = lists_response.json()

    # Find the list with displayName 'Files'
    filesList_data = None
    for list_item in team_lists['value']:
        if list_item['displayName'] == 'Files':
            filesList_data = list_item
            break

    if not filesList_data:
        return "No list with displayName 'Files' found."
    get_contacts(f'https://graph.microsoft.com/v1.0/sites/{site_id}/lists/{filesList_data["id"]}/items?expand=fields', token["access_token"])

def save_data_to_json(data, filename):
    with open(filename, 'w') as file:
        json.dump(data, file, indent=4)

def read_json_file(filename):
    with open(filename, 'r') as file:
        data = json.load(file)
    return data 

def get_contacts(url, token, all_contacts=None):
    if all_contacts is None:
        all_contacts = []
    try:
        response = requests.get(
            url,
            headers={'Authorization': f'Bearer {token}'},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        contacts = data['value']
        all_contacts.extend(contacts)
        next_link = data['@odata.nextLink']
        if next_link:
            get_contacts(next_link, token, all_contacts)
    except requests.exceptions.RequestException as e:
        print(f"Error fetching contacts: {e}")
    except KeyError:
        print("Missing key in response.")
    save_data_to_json(all_contacts, 'clients.json')

@app.route('/get-site', methods=['GET', 'POST'])
def get_site():
    global background_task_running
    if background_task_running:
        flash("This route is temporarily disabled while a background task is running."), 503
        return redirect(url_for('index'))
    if request.method == 'GET':
        clients = None
        try:
            clients = read_json_file('clients.json')
        except FileNotFoundError:
            clients = None  # Handle the case where the file doesn't exist
        return render_template('get-site.html', clients=clients)
    elif request.method == 'POST':
        # Handle POST request here if necessary
        pass
        
if __name__ == "__main__":
    app.run()
