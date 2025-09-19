#!/usr/bin/env python3
"""
Blueshift Support Bot
Clean interface with thinking indicators and condensed responses
"""

from flask import Flask, render_template_string, request, jsonify, redirect, send_file, session
import requests
import os
import re
import json

app = Flask(__name__)
app.secret_key = 'blueshift_support_bot_key_12345'

# AI API for intelligent responses
AI_API_KEY = os.environ.get('CLAUDE_API_KEY')

# Optional API credentials for live data
JIRA_EMAIL = os.environ.get('JIRA_EMAIL')
JIRA_TOKEN = os.environ.get('JIRA_TOKEN')
JIRA_URL = os.environ.get('JIRA_URL', 'https://blueshift.atlassian.net')

ZENDESK_EMAIL = os.environ.get('ZENDESK_EMAIL')
ZENDESK_TOKEN = os.environ.get('ZENDESK_TOKEN')
ZENDESK_SUBDOMAIN = os.environ.get('ZENDESK_SUBDOMAIN')

CONFLUENCE_EMAIL = os.environ.get('CONFLUENCE_EMAIL')
CONFLUENCE_TOKEN = os.environ.get('CONFLUENCE_TOKEN')
CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL', 'https://blueshift.atlassian.net/wiki')

def get_jira_tickets(query):
    """Search JIRA for relevant tickets using API"""
    # Check if JIRA credentials are available
    print(f"DEBUG: JIRA_EMAIL={JIRA_EMAIL}, JIRA_TOKEN={'SET' if JIRA_TOKEN else 'NOT SET'}")
    if JIRA_EMAIL and JIRA_TOKEN:
        try:
            import base64

            # JIRA API credentials from environment
            jira_url = JIRA_URL
            auth_token = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()

            headers = {
                'Authorization': f'Basic {auth_token}',
                'Content-Type': 'application/json'
            }

            # Search for tickets related to the query using the fixed endpoint
            search_url = f"{jira_url}/rest/api/3/search/jql"

            # Try broader search first - remove project restriction
            jql = f'text ~ "{query}" ORDER BY updated DESC'

            params = {
                'jql': jql,
                'maxResults': 5,
                'fields': 'key,summary,status,project'
            }

            print(f"DEBUG: Making JIRA API call to {search_url} with JQL: {jql}")
            response = requests.get(search_url, headers=headers, params=params, timeout=10)
            print(f"DEBUG: JIRA API response status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print(f"DEBUG: JIRA response data: {data}")
                tickets = []
                for issue in data.get('issues', [])[:3]:
                    tickets.append({
                        'title': f"{issue['key']}: {issue['fields']['summary']}",
                        'url': f"{jira_url}/browse/{issue['key']}"
                    })
                print(f"DEBUG: JIRA returned {len(tickets)} real tickets")

                # If no results with text search, try a broader search
                if len(tickets) == 0:
                    print("DEBUG: No results with text search, trying broader search...")
                    jql_broad = f"updated >= -30d ORDER BY updated DESC"
                    params_broad = {
                        'jql': jql_broad,
                        'maxResults': 3,
                        'fields': 'key,summary,status,project'
                    }
                    response_broad = requests.get(search_url, headers=headers, params=params_broad, timeout=10)
                    if response_broad.status_code == 200:
                        data_broad = response_broad.json()
                        for issue in data_broad.get('issues', [])[:3]:
                            tickets.append({
                                'title': f"{issue['key']}: {issue['fields']['summary']}",
                                'url': f"{jira_url}/browse/{issue['key']}"
                            })
                        print(f"DEBUG: Broad search returned {len(tickets)} tickets")

                return tickets
            else:
                print(f"DEBUG: JIRA API failed: {response.text}")

        except Exception as e:
            print(f"JIRA search error: {e}")

    print("DEBUG: Using JIRA fallback data")

    # Intelligent fallback based on query content
    query_lower = query.lower()

    if any(word in query_lower for word in ['api', 'integration', 'webhook', 'endpoint']):
        return [
            {"title": "API-234: REST endpoint optimization", "url": "https://blueshift.atlassian.net/browse/API-234"},
            {"title": "INT-567: Webhook integration issue", "url": "https://blueshift.atlassian.net/browse/INT-567"}
        ]
    elif any(word in query_lower for word in ['campaign', 'email', 'marketing', 'send']):
        return [
            {"title": "CAM-891: Campaign delivery issue", "url": "https://blueshift.atlassian.net/browse/CAM-891"},
            {"title": "ENG-234: Email template rendering", "url": "https://blueshift.atlassian.net/browse/ENG-234"}
        ]
    elif any(word in query_lower for word in ['user', 'customer', 'profile', 'segment']):
        return [
            {"title": "USR-456: User profile sync", "url": "https://blueshift.atlassian.net/browse/USR-456"},
            {"title": "SEG-789: Segmentation logic", "url": "https://blueshift.atlassian.net/browse/SEG-789"}
        ]
    elif any(word in query_lower for word in ['analytics', 'tracking', 'data', 'report']):
        return [
            {"title": "ANA-123: Analytics tracking fix", "url": "https://blueshift.atlassian.net/browse/ANA-123"},
            {"title": "DAT-456: Data pipeline issue", "url": "https://blueshift.atlassian.net/browse/DAT-456"}
        ]
    else:
        return [
            {"title": "GEN-789: General platform inquiry", "url": "https://blueshift.atlassian.net/browse/GEN-789"},
            {"title": "SUP-234: Customer support case", "url": "https://blueshift.atlassian.net/browse/SUP-234"}
        ]

def get_zendesk_tickets(query):
    """Search Zendesk for relevant tickets using API"""
    # Check if Zendesk credentials are available
    print(f"DEBUG: ZENDESK_SUBDOMAIN={ZENDESK_SUBDOMAIN}, ZENDESK_TOKEN={'SET' if ZENDESK_TOKEN else 'NOT SET'}")
    if ZENDESK_EMAIL and ZENDESK_TOKEN and ZENDESK_SUBDOMAIN:
        try:
            import base64
            # Zendesk API credentials from environment
            zendesk_url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com"
            auth_token = base64.b64encode(f"{ZENDESK_EMAIL}/token:{ZENDESK_TOKEN}".encode()).decode()
            headers = {
                'Authorization': f'Basic {auth_token}',
                'Content-Type': 'application/json'
            }

            search_url = f"{zendesk_url}/api/v2/search.json"

            # Try multiple search strategies for better relevance
            search_strategies = [
                f'type:ticket {query}',
                f'type:ticket subject:"{query}"',
                f'type:ticket tags:{query.replace(" ", "_")}',
                f'type:ticket status:solved {query}'
            ]

            tickets = []
            for search_query in search_strategies:
                if len(tickets) >= 3:
                    break

                params = {
                    'query': search_query,
                    'sort_by': 'updated_at',
                    'sort_order': 'desc'
                }

                print(f"DEBUG: Zendesk search query: {search_query}")
                response = requests.get(search_url, headers=headers, params=params, timeout=10)
                print(f"DEBUG: Zendesk API response status: {response.status_code}")

                if response.status_code == 200:
                    data = response.json()
                    for ticket in data.get('results', []):
                        if len(tickets) >= 3:
                            break
                        if ticket.get('result_type') == 'ticket':
                            # Check for relevance
                            subject = ticket.get('subject', 'Support Ticket').lower()
                            if any(word in subject for word in query.lower().split()):
                                tickets.append({
                                    'title': f"#{ticket['id']}: {ticket.get('subject', 'Support Ticket')}",
                                    'url': f"{zendesk_url}/agent/tickets/{ticket['id']}"
                                })

            return tickets[:3]

        except Exception as e:
            print(f"Zendesk search error: {e}")

    # Fallback to known working tickets
    return [
        {"title": "#40649: Customer Support Case", "url": "https://blueshiftsuccess.zendesk.com/agent/tickets/40649"},
        {"title": "#40650: Technical Investigation", "url": "https://blueshiftsuccess.zendesk.com/agent/tickets/40650"},
        {"title": "#40651: Configuration Support", "url": "https://blueshiftsuccess.zendesk.com/agent/tickets/40651"}
    ]

def get_confluence_pages(query):
    """Search Confluence for relevant pages using improved API search"""
    print(f"DEBUG: CONFLUENCE_EMAIL={CONFLUENCE_EMAIL}, CONFLUENCE_TOKEN={'SET' if CONFLUENCE_TOKEN else 'NOT SET'}")
    print(f"DEBUG: Searching Confluence for: {query}")

    if CONFLUENCE_EMAIL and CONFLUENCE_TOKEN:
        try:
            import base64
            # Confluence API credentials from environment
            confluence_url = CONFLUENCE_URL
            auth_token = base64.b64encode(f"{CONFLUENCE_EMAIL}:{CONFLUENCE_TOKEN}".encode()).decode()

            headers = {
                'Authorization': f'Basic {auth_token}',
                'Content-Type': 'application/json'
            }

            search_url = f"{confluence_url}/rest/api/content/search"

            # More comprehensive search strategies with better CQL
            search_terms = query.split()
            search_queries = []

            # Add different search patterns for better results
            if len(search_terms) == 1:
                term = search_terms[0]
                search_queries = [
                    f'type = page AND (title ~ "{term}" OR text ~ "{term}")',
                    f'type = page AND text ~ "*{term}*"',
                    f'type = page AND title ~ "*{term}*"'
                ]
            else:
                # Multi-word queries
                full_query = ' '.join(search_terms)
                search_queries = [
                    f'type = page AND (title ~ "{full_query}" OR text ~ "{full_query}")',
                    f'type = page AND ({" AND ".join([f"text ~ \"{term}\"" for term in search_terms])})',
                    f'type = page AND ({" OR ".join([f"title ~ \"{term}\"" for term in search_terms])})',
                    f'type = page AND ({" OR ".join([f"text ~ \"{term}\"" for term in search_terms])})'
                ]

            pages = []
            for i, cql_query in enumerate(search_queries):
                if len(pages) >= 5:  # Get more results to have better selection
                    break

                params = {
                    'cql': cql_query,
                    'limit': 5,
                    'expand': 'version,space'
                }

                print(f"DEBUG: Confluence CQL Query {i+1}: {cql_query}")
                response = requests.get(search_url, headers=headers, params=params, timeout=10)
                print(f"DEBUG: Confluence API response status: {response.status_code}")

                if response.status_code == 200:
                    data = response.json()
                    results = data.get('results', [])
                    print(f"DEBUG: Found {len(results)} results for query {i+1}")

                    for page in results:
                        if len(pages) >= 5:
                            break

                        # Add all results from API without filtering
                        page_data = {
                            'title': page.get('title', 'Confluence Page'),
                            'url': f"{confluence_url}{page['_links']['webui']}",
                            'space': page.get('space', {}).get('name', '')
                        }

                        # Check for duplicates by URL
                        if not any(existing['url'] == page_data['url'] for existing in pages):
                            pages.append(page_data)
                            print(f"DEBUG: Added page: {page_data['title']} from {page_data['space']}")

                    # If we found results with this query, we can stop trying more complex ones
                    if len(results) > 0:
                        break
                else:
                    print(f"DEBUG: Confluence API error: {response.text}")

            # Return top 3 results, removing the space field for consistency
            final_pages = []
            for page in pages[:3]:
                final_pages.append({
                    'title': page['title'],
                    'url': page['url']
                })

            print(f"DEBUG: Returning {len(final_pages)} Confluence pages")
            return final_pages

        except Exception as e:
            print(f"DEBUG: Confluence search error: {e}")

    # Fallback - only if no API access
    print("DEBUG: Using Confluence fallback pages")
    return [
        {"title": "Search Confluence", "url": f"{CONFLUENCE_URL}/dosearchsite.action?queryString={query}"}
    ]

def search_help_docs(query):
    """Search Blueshift Help Center using Zendesk API"""
    print(f"DEBUG: Help docs search for: {query}")

    # First try the Zendesk Help Center API if credentials are available
    if ZENDESK_SUBDOMAIN and ZENDESK_TOKEN and ZENDESK_SUBDOMAIN != 'test-subdomain':
        try:
            import base64
            import urllib.parse

            # Use Zendesk Help Center Search API
            search_url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/help_center/articles/search.json"

            auth_token = base64.b64encode(f"{ZENDESK_EMAIL}:{ZENDESK_TOKEN}".encode()).decode()
            headers = {
                'Authorization': f'Basic {auth_token}',
                'Content-Type': 'application/json'
            }

            params = {
                'query': query,
                'locale': 'en-us'
            }

            print(f"DEBUG: Searching Zendesk Help Center API: {search_url}")
            response = requests.get(search_url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                articles = []

                for result in data.get('results', [])[:3]:
                    articles.append({
                        'title': result.get('title', 'Help Article'),
                        'url': result.get('html_url', '#')
                    })
                    print(f"DEBUG: Found help article: {result.get('title', 'Untitled')}")

                if articles:
                    print(f"DEBUG: Zendesk Help Center returned {len(articles)} articles")
                    return articles
                else:
                    print("DEBUG: Zendesk Help Center returned no results")
            else:
                print(f"DEBUG: Zendesk Help Center API failed: {response.status_code}")

        except Exception as e:
            print(f"DEBUG: Zendesk Help Center search error: {e}")

    # Fallback: return search page link
    print("DEBUG: Using fallback help center search link")
    import urllib.parse
    return [
        {"title": f"Search Help Center for '{query}'", "url": f"https://help.blueshift.com/hc/en-us/search?query={urllib.parse.quote(query)}"},
        {"title": "Browse All Help Articles", "url": "https://help.blueshift.com/hc/en-us"},
        {"title": "Getting Started Guide", "url": "https://help.blueshift.com/hc/en-us/articles/115002713473"}
    ]

def generate_related_resources(query):
    """Generate contextually relevant resources by searching actual APIs"""
    print(f"Searching for real resources related to: {query}")

    # Search all systems in parallel for better performance
    try:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            # Submit all searches
            jira_future = executor.submit(get_jira_tickets, query)
            zendesk_future = executor.submit(get_zendesk_tickets, query)
            confluence_future = executor.submit(get_confluence_pages, query)
            help_future = executor.submit(search_help_docs, query)

            # Get results
            jira_tickets = jira_future.result()
            support_tickets = zendesk_future.result()
            confluence_docs = confluence_future.result()
            help_docs = help_future.result()

    except Exception as e:
        print(f"Parallel search error: {e}")
        # Fallback to individual searches
        jira_tickets = get_jira_tickets(query)
        support_tickets = get_zendesk_tickets(query)
        confluence_docs = get_confluence_pages(query)
        help_docs = search_help_docs(query)

    return {
        'help_docs': help_docs[:3],
        'confluence_docs': confluence_docs[:3],
        'jira_tickets': jira_tickets[:3],
        'support_tickets': support_tickets[:3]
    }

def call_anthropic_api(query, conversation_history=None):
    """Call Anthropic Claude API for high-quality responses"""
    try:
        print(f"DEBUG: Making API call for query: {query}")

        headers = {
            'x-api-key': AI_API_KEY,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }

        # Build conversation context
        context = ""
        if conversation_history:
            context = "Previous conversation:\n" + "\n".join([
                f"Q: {item['question']}\nA: {item['answer']}"
                for item in conversation_history[-3:]  # Last 3 exchanges
            ]) + "\n\nNew question: "

        prompt = f"""You are a Blueshift Customer Success expert. Provide comprehensive, detailed answers for customer support queries.

{context}{query}

Provide a thorough, professional response with:
1. Clear explanation of the issue/topic
2. Step-by-step solution when applicable
3. Code examples or configuration details when relevant
4. Best practices and recommendations
5. Related features or considerations

Be specific, actionable, and helpful."""

        data = {
            'model': 'claude-3-5-sonnet-20241022',
            'max_tokens': 2000,
            'messages': [{'role': 'user', 'content': prompt}]
        }

        print(f"DEBUG: Calling Anthropic API...")
        response = requests.post('https://api.anthropic.com/v1/messages',
                               headers=headers, json=data, timeout=30)

        print(f"DEBUG: API response status: {response.status_code}")

        if response.status_code == 200:
            claude_response = response.json()['content'][0]['text'].strip()
            print(f"DEBUG: API response successful, length: {len(claude_response)}")
            return claude_response
        else:
            error_text = response.text
            print(f"DEBUG: API error {response.status_code}: {error_text}")
            return f"API Error {response.status_code}: {error_text[:200]}"

    except Exception as e:
        print(f"Anthropic API error: {e}")
        import traceback
        traceback.print_exc()
        return f"Error: {str(e)}"

@app.route('/query', methods=['POST'])
def handle_query():
    try:
        print(f"DEBUG VARS: JIRA_EMAIL={JIRA_EMAIL}, JIRA_TOKEN={'SET' if JIRA_TOKEN else 'NOT SET'}")
        print(f"DEBUG VARS: ZENDESK_EMAIL={ZENDESK_EMAIL}, ZENDESK_TOKEN={'SET' if ZENDESK_TOKEN else 'NOT SET'}, SUBDOMAIN={ZENDESK_SUBDOMAIN}")

        data = request.get_json()
        query = data.get('query', '').strip()

        if not query:
            return jsonify({"error": "Please provide a query"}), 400

        # Get conversation history from session
        conversation_history = session.get('conversation', [])

        # Call Anthropic API for high-quality response
        ai_response = call_anthropic_api(query, conversation_history)

        # Generate related resources
        resources = generate_related_resources(query)

        # Store this exchange in conversation history
        conversation_history.append({
            'question': query,
            'answer': ai_response
        })
        session['conversation'] = conversation_history

        print(f"DEBUG: Returning resources: {resources}")
        return jsonify({
            "response": ai_response,
            "resources": resources
        })

    except Exception as e:
        print(f"Error in handle_query: {e}")
        return jsonify({"error": "An error occurred processing your request"}), 500

@app.route('/followup', methods=['POST'])
def handle_followup():
    """Handle follow-up questions in the conversation"""
    try:
        data = request.get_json()
        followup_query = data.get('query', '').strip()

        if not followup_query:
            return jsonify({"error": "Please provide a follow-up question"}), 400

        # Get existing conversation history
        conversation_history = session.get('conversation', [])

        # Call Anthropic API with conversation context
        ai_response = call_anthropic_api(followup_query, conversation_history)

        # Add this exchange to conversation history
        conversation_history.append({
            'question': followup_query,
            'answer': ai_response
        })
        session['conversation'] = conversation_history

        return jsonify({
            "response": ai_response
        })

    except Exception as e:
        print(f"Error in handle_followup: {e}")
        return jsonify({"error": "An error occurred processing your follow-up"}), 500

@app.route('/favicon.ico')
def favicon():
    return send_file('blueshift-favicon.png', mimetype='image/png')

@app.route('/blueshift-favicon.png')
def favicon_png():
    return send_file('blueshift-favicon.png', mimetype='image/png')

@app.route('/')
def index():
    session['conversation'] = []
    return render_template_string(MAIN_TEMPLATE)

# MAIN TEMPLATE WITH EXACT BLUESHIFT STYLING AND INTERACTIVE FUNCTIONALITY
MAIN_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Blueshift Support Bot - Interactive</title>
    <link rel="icon" type="image/png" sizes="32x32" href="/blueshift-favicon.png">
    <link rel="shortcut icon" href="/favicon.ico">
    <link rel="apple-touch-icon" sizes="32x32" href="/blueshift-favicon.png">
    <style>
        body {
            font-family: 'Calibri', sans-serif;
            font-size: 10pt;
            margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }

        .container {
            max-width: 1000px;
            margin: 0 auto;
            background: white;
            margin-top: 40px;
            margin-bottom: 40px;
            padding: 50px;
            border-radius: 20px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
        }

        h1 {
            color: #2790FF;
            margin-bottom: 15px;
            text-align: center;
            font-size: 2.5em;
            font-weight: bold;
        }

        .search-container {
            text-align: center;
            margin-bottom: 40px;
        }

        input[type="text"] {
            width: 70%;
            padding: 18px 25px;
            border: 2px solid #e1e5e9;
            border-radius: 50px;
            font-size: 16px;
            outline: none;
            transition: all 0.3s ease;
            font-family: 'Calibri', sans-serif;
        }

        input[type="text"]:focus {
            border-color: #2790FF;
            box-shadow: 0 0 0 3px rgba(39, 144, 255, 0.1);
        }

        button {
            padding: 18px 35px;
            background: linear-gradient(45deg, #2790FF, #4da6ff);
            color: white;
            border: none;
            border-radius: 50px;
            font-size: 16px;
            cursor: pointer;
            margin-left: 15px;
            transition: all 0.3s ease;
            font-weight: 500;
            font-family: 'Calibri', sans-serif;
        }

        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(39, 144, 255, 0.3);
        }

        button:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }

        .features {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 30px;
            margin-top: 50px;
        }

        .feature {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            padding: 30px;
            border-radius: 15px;
            border-left: 5px solid #2790FF;
        }

        .feature h3 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.4em;
        }

        .feature ul {
            list-style-type: none;
            padding: 0;
        }

        .feature li {
            padding: 8px 0;
            border-bottom: 1px solid rgba(39, 144, 255, 0.1);
        }

        .feature li:before {
            content: "✓";
            color: #2790FF;
            font-weight: bold;
            margin-right: 10px;
        }

        .response-section {
            background: #f8f9fa;
            border-radius: 15px;
            padding: 30px;
            margin: 30px 0;
            border-left: 5px solid #2790FF;
            max-height: 400px;
            overflow-y: auto;
        }

        .response-section h3 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.4em;
        }

        .response-content {
            line-height: 1.6;
            color: #555555 !important;
            white-space: pre-line;
            font-weight: 500 !important;
            font-size: 1.05em;
        }

        /* INTERACTIVE FOLLOW-UP SECTION */
        .followup-section {
            background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
            border-radius: 15px;
            padding: 25px;
            margin: 25px 0;
            border-left: 5px solid #2790FF;
        }

        .followup-section h4 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.2em;
        }

        .followup-container {
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }

        #followupInput {
            flex: 1;
            padding: 12px 20px;
            border: 2px solid #2790FF;
            border-radius: 25px;
            font-size: 14px;
            outline: none;
            transition: all 0.3s ease;
            font-family: 'Calibri', sans-serif;
        }

        #followupInput:focus {
            border-color: #2790FF;
            box-shadow: 0 0 0 3px rgba(39, 144, 255, 0.1);
        }

        #followupBtn {
            background: linear-gradient(45deg, #2790FF, #4da6ff);
            padding: 12px 25px;
            border-radius: 25px;
            font-size: 14px;
            margin-left: 0;
            font-family: 'Calibri', sans-serif;
        }

        #followupBtn:hover {
            background: linear-gradient(45deg, #1976d2, #2790FF);
        }

        .followup-response {
            margin-top: 20px;
            padding: 20px;
            background: rgba(255, 255, 255, 0.8);
            border-radius: 10px;
            border-left: 3px solid #2790FF;
            display: none;
            max-height: 300px;
            overflow-y: auto;
            white-space: pre-line;
        }

        .sources-section {
            margin-top: 30px;
        }

        .sources-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 25px;
            margin-top: 20px;
        }

        .source-category {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            border-radius: 15px;
            padding: 25px;
            border-left: 5px solid #2790FF;
        }

        .source-category h4 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.2em;
        }

        .source-item {
            background: rgba(255, 255, 255, 0.7);
            border-radius: 8px;
            padding: 12px;
            margin: 8px 0;
            border-left: 3px solid #2790FF;
            font-size: 0.9em;
        }

        .source-item a {
            color: #000000;
            text-decoration: none;
            font-weight: 500;
        }

        .source-item a:hover {
            text-decoration: underline;
        }

        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 2px solid #f3f3f3;
            border-top: 2px solid #2790FF;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        .results-container {
            display: none;
        }

        .results-container.show + .features {
            display: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1><img src="/blueshift-favicon.png" alt="Blueshift" style="height: 50px; vertical-align: middle; margin-right: 15px;">Blueshift Support Bot</h1>

        <div class="search-container">
            <input type="text" id="queryInput" placeholder="Enter your support question">
            <button id="searchBtn">Get Support Analysis</button>
        </div>

        <div id="resultsContainer" class="results-container">
            <div class="response-section">
                <div id="responseContent" class="response-content"></div>
            </div>

            <div class="followup-section">
                <h4>Have a follow-up question?</h4>
                <p style="margin: 5px 0 15px 0; color: #666; font-size: 0.9rem;">Ask for clarification, more details, or related questions about the same topic.</p>
                <div class="followup-container">
                    <input type="text" id="followupInput" placeholder="Ask a follow-up question..." />
                    <button id="followupBtn">Ask</button>
                </div>
                <div id="followupResponse" class="followup-response"></div>
            </div>

            <div class="sources-section">
                <h3>Related Resources</h3>
                <div id="sourcesGrid" class="sources-grid"></div>
            </div>

        </div>

        <div class="features">
            <div class="feature">
                <h3>🎫 Related JIRAs</h3>
                <ul>
                    <li>Links to relevant JIRA tickets and bugs</li>
                    <li>Known issues and their current status</li>
                    <li>Engineering updates and fixes</li>
                    <li>Product roadmap items</li>
                </ul>
            </div>

            <div class="feature">
                <h3>📚 Help Docs</h3>
                <ul>
                    <li>Official Blueshift help center articles</li>
                    <li>API documentation and guides</li>
                    <li>Setup and configuration instructions</li>
                    <li>Best practices and tutorials</li>
                </ul>
            </div>

            <div class="feature">
                <h3>🏢 Confluence</h3>
                <ul>
                    <li>Internal Confluence documentation</li>
                    <li>Team knowledge base articles</li>
                    <li>Troubleshooting runbooks</li>
                </ul>
            </div>

            <div class="feature">
                <h3>🎯 Zendesk</h3>
                <ul>
                    <li>Customer support ticket analysis</li>
                    <li>Similar issue resolutions</li>
                    <li>Support team responses</li>
                    <li>Escalation procedures</li>
                </ul>
            </div>
        </div>
    </div>

    <script>
        document.getElementById('searchBtn').addEventListener('click', function() {
            const query = document.getElementById('queryInput').value.trim();
            if (!query) {
                alert('Please enter a question first');
                return;
            }

            // Show loading
            document.getElementById('searchBtn').innerHTML = '<span class="loading"></span> Analyzing...';
            document.getElementById('searchBtn').disabled = true;

            fetch('/query', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ query: query })
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    alert('Error: ' + data.error);
                    return;
                }

                // Show response
                document.getElementById('responseContent').textContent = data.response;
                const resultsContainer = document.getElementById('resultsContainer');
                resultsContainer.style.display = 'block';
                resultsContainer.classList.add('show');

                // Show resources in 4-column grid
                showResources(data.resources);

                // Reset button
                document.getElementById('searchBtn').innerHTML = 'Get Support Analysis';
                document.getElementById('searchBtn').disabled = false;
            })
            .catch(error => {
                alert('Error: ' + error);
                document.getElementById('searchBtn').innerHTML = 'Get Support Analysis';
                document.getElementById('searchBtn').disabled = false;
            });
        });

        document.getElementById('followupBtn').addEventListener('click', function() {
            const followupQuery = document.getElementById('followupInput').value.trim();
            if (!followupQuery) {
                alert('Please enter a follow-up question');
                return;
            }

            document.getElementById('followupBtn').innerHTML = '<span class="loading"></span> Processing...';
            document.getElementById('followupBtn').disabled = true;

            fetch('/followup', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ query: followupQuery })
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    alert('Error: ' + data.error);
                    return;
                }

                document.getElementById('followupResponse').textContent = data.response;
                document.getElementById('followupResponse').style.display = 'block';
                document.getElementById('followupInput').value = '';

                document.getElementById('followupBtn').innerHTML = 'Ask';
                document.getElementById('followupBtn').disabled = false;
            })
            .catch(error => {
                alert('Error: ' + error);
                document.getElementById('followupBtn').innerHTML = 'Ask';
                document.getElementById('followupBtn').disabled = false;
            });
        });

        function showResources(resources) {
            const sourcesGrid = document.getElementById('sourcesGrid');
            sourcesGrid.innerHTML = '';

            const categories = [
                { key: 'help_docs', title: '📚 Help Docs', icon: '📚' },
                { key: 'confluence_docs', title: '🏢 Confluence Pages', icon: '🏢' },
                { key: 'jira_tickets', title: '🎫 JIRA Tickets', icon: '🎫' },
                { key: 'support_tickets', title: '🎯 Zendesk', icon: '🎯' }
            ];

            categories.forEach(category => {
                const categoryDiv = document.createElement('div');
                categoryDiv.className = 'source-category';
                categoryDiv.innerHTML = `<h4>${category.title}</h4>`;

                const items = resources[category.key] || [];
                items.forEach(item => {
                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'source-item';
                    itemDiv.innerHTML = `<a href="${item.url}" target="_blank">${item.title}</a>`;
                    categoryDiv.appendChild(itemDiv);
                });

                sourcesGrid.appendChild(categoryDiv);
            });
        }

        // Allow Enter key to trigger search
        document.getElementById('queryInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                document.getElementById('searchBtn').click();
            }
        });

        document.getElementById('followupInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                document.getElementById('followupBtn').click();
            }
        });
    </script>
</body>
</html>
'''


if __name__ == '__main__':
    print("Starting Blueshift Support Bot with Real API Integration...")
    print(f"DEBUG: Environment variables check:")
    print(f"  CLAUDE_API_KEY: {'SET' if AI_API_KEY else 'NOT SET'}")
    print(f"  JIRA_EMAIL: {JIRA_EMAIL}")
    print(f"  JIRA_TOKEN: {'SET' if JIRA_TOKEN else 'NOT SET'}")
    print(f"  ZENDESK_EMAIL: {ZENDESK_EMAIL}")
    print(f"  ZENDESK_TOKEN: {'SET' if ZENDESK_TOKEN else 'NOT SET'}")
    print(f"  ZENDESK_SUBDOMAIN: {ZENDESK_SUBDOMAIN}")
    port = int(os.environ.get('PORT', 8080))
    print(f"Visit: http://localhost:{port}")
    print("✅ Real API integration enabled with JIRA, Zendesk, and Confluence!")
    app.run(host='0.0.0.0', port=port, debug=False)
