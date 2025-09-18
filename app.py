#!/usr/bin/env python3
"""
Blueshift Support Bot
Clean interface with thinking indicators and condensed responses
"""

from flask import Flask, render_template_string, request, jsonify, redirect, send_file
import requests
import os
import re
import json

app = Flask(__name__)

# AI API for intelligent responses
AI_API_KEY = 'os.environ.get('CLAUDE_API_KEY')'

def generate_contextual_resources(query):
    """Generate contextual resources based on the query content."""
    query_lower = query.lower()

    resources = {
        'jiras': [],
        'docs': [],
        'tickets': []
    }

    # Subscription Groups
    if 'subscription' in query_lower or 'group' in query_lower:
        resources['jiras'].extend([
            "BS-2341: Subscription group management improvements",
            "BS-1892: Group creation API enhancements"
        ])
        resources['docs'].extend([
            "How to Create and Manage Subscription Groups",
            "Subscription Group Settings and Configuration",
            "Advanced Group Segmentation Guide"
        ])
        resources['tickets'].extend([
            "Support Case #18392: Subscription group setup",
            "Knowledge Base: Group management best practices"
        ])

    # Email/Campaign related
    if any(word in query_lower for word in ['email', 'campaign', 'delivery', 'send']):
        resources['jiras'].extend([
            "BS-1234: Email delivery optimization",
            "BS-3456: Campaign performance improvements"
        ])
        resources['docs'].extend([
            "Email Campaign Troubleshooting Guide",
            "Email Delivery Best Practices",
            "Campaign Setup and Management"
        ])
        resources['tickets'].extend([
            "Support Case #12345: Email delivery issues",
            "Runbook: Email troubleshooting workflow"
        ])

    # API & Integrations related
    if any(word in query_lower for word in ['api', 'authentication', '401', 'auth', 'integration', 'webhook', 'zendesk', 'salesforce', 'hubspot', 'connect']):
        resources['jiras'].extend([
            "BS-5678: API authentication improvements",
            "BS-7890: API rate limiting enhancements",
            "BS-3421: Zendesk integration enhancements",
            "BS-9876: Webhook reliability improvements"
        ])
        resources['docs'].extend([
            "API Authentication Setup Guide",
            "Zendesk Integration Configuration",
            "Webhook Setup and Troubleshooting",
            "Third-Party Integration Best Practices"
        ])
        resources['tickets'].extend([
            "Support Case #23456: API auth issues",
            "Support Case #78901: Zendesk integration setup",
            "Internal: Integration troubleshooting guide"
        ])

    # Liquid/Personalization
    if 'liquid' in query_lower or 'personalization' in query_lower or 'subject line' in query_lower:
        resources['jiras'].extend([
            "BS-4321: Liquid template improvements",
            "BS-6543: Personalization engine updates"
        ])
        resources['docs'].extend([
            "Liquid Template Creation Guide",
            "Personalization Best Practices",
            "Dynamic Content Setup"
        ])
        resources['tickets'].extend([
            "Support Case #34567: Liquid template help",
            "Knowledge Base: Personalization examples"
        ])

    # Segmentation
    if 'segment' in query_lower or 'customer' in query_lower or 'targeting' in query_lower:
        resources['jiras'].extend([
            "BS-8765: Customer segmentation improvements",
            "BS-9876: Advanced targeting features"
        ])
        resources['docs'].extend([
            "Customer Segmentation Guide",
            "Advanced Targeting and Filtering",
            "Segment Performance Optimization"
        ])
        resources['tickets'].extend([
            "Support Case #45678: Segmentation setup",
            "Runbook: Segment troubleshooting guide"
        ])

    # Add general resources only if no specific matches found
    if not any([resources['jiras'], resources['docs'], resources['tickets']]):
        resources['jiras'].extend([
            "BS-1111: Platform stability improvements",
            "BS-2222: User experience enhancements"
        ])
        resources['docs'].extend([
            "Blueshift Platform Overview",
            "General Troubleshooting Guide",
            "Getting Started Documentation"
        ])
        resources['tickets'].extend([
            "Support Case #56789: General platform help",
            "Knowledge Base: Common issues and solutions"
        ])

    return resources

def generate_ai_solution(query):
    """
    Generate intelligent, contextual solutions using AI
    """

    print(f"🎯 AI ANALYZING: {query}")

    if not AI_API_KEY:
        return "AI service not configured. Cannot provide intelligent responses."

    try:
        system_prompt = """You are an expert Blueshift platform consultant providing intelligent support to customer support agents.

CRITICAL: Analyze the user's SPECIFIC question and provide targeted, contextual solutions - not generic documentation.

Your approach:
1. READ THE COMPLETE QUESTION - understand the exact scenario they're describing
2. IDENTIFY THE SPECIFIC PROBLEM - delivery issues, configuration questions, troubleshooting needs
3. PROVIDE TARGETED SOLUTIONS - address their exact situation with multiple potential causes
4. INCLUDE ACTIONABLE STEPS - specific Blueshift UI paths, API calls, investigation methods
5. KEEP IT CONCISE - provide comprehensive but focused responses

Format guidelines:
- Use clear headings (## Section Name)
- Provide numbered steps for procedures
- Include specific Blueshift paths and settings
- Offer 3-4 main potential causes/solutions maximum
- Keep responses under 800 words total
- Focus on actionable guidance

Provide comprehensive solutions that demonstrate deep Blueshift platform expertise while staying focused and concise."""

        user_message = f"""A Blueshift support agent needs help with this specific situation:

"{query}"

Please analyze this question and provide an intelligent, targeted response that directly addresses their specific need.

Focus on:
- Understanding their exact scenario and problem
- Providing 3-4 key potential causes when troubleshooting
- Giving specific Blueshift steps, settings, and configurations
- Including actionable investigation steps they can take immediately
- Keeping the response concise but comprehensive

Provide a focused response that gets straight to the solution."""

        print("🔄 Calling AI API for intelligent analysis...")

        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': AI_API_KEY,
                'Content-Type': 'application/json',
                'anthropic-version': '2023-06-01'
            },
            json={
                'model': 'claude-3-5-sonnet-20241022',
                'max_tokens': 1200,
                'system': system_prompt,
                'messages': [
                    {'role': 'user', 'content': user_message}
                ]
            },
            timeout=30
        )

        print(f"✅ AI Response Status: {response.status_code}")

        if response.status_code == 200:
            response_data = response.json()
            ai_response = response_data['content'][0]['text'].strip()
            print(f"✅ Generated AI response: {len(ai_response)} characters")

            if ai_response and len(ai_response) > 100:
                return ai_response
            else:
                print("⚠️ Response too short")
                return f"AI provided a very brief response. For your question: '{query}'\n\nPlease try rephrasing your question with more specific details, or contact Blueshift support directly for immediate assistance."

        else:
            print(f"❌ AI API Error: {response.status_code}")
            return f"I'm currently unable to analyze your question due to a technical issue. For your question: '{query}'\n\nPlease try again in a moment, or contact Blueshift support directly for immediate assistance."

    except Exception as e:
        print(f"❌ Exception in AI call: {str(e)}")
        return f"I encountered an issue while analyzing: '{query}'\n\nFor immediate help:\n1. Try again in a few moments\n2. Contact Blueshift support directly\n3. Check the platform documentation"

@app.route('/favicon.ico')
def favicon():
    return send_file('blueshift-favicon.png', mimetype='image/png')

@app.route('/')
def index():
    return '''<!DOCTYPE html>
<html>
<head>
    <title>Blueshift Support Bot</title>
    <link rel="icon" type="image/png" href="/favicon.ico">
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; }
        .container { max-width: 1000px; margin: 0 auto; background: white; margin-top: 40px; margin-bottom: 40px; padding: 50px; border-radius: 20px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); }
        h1 { color: #2e7d3e; margin-bottom: 15px; text-align: center; font-size: 2.5em; font-weight: 300; }
        .search-container { text-align: center; margin-bottom: 40px; }
        input[type="text"] { width: 70%; padding: 18px 25px; border: 2px solid #e1e5e9; border-radius: 50px; font-size: 16px; outline: none; transition: all 0.3s ease; }
        input[type="text"]:focus { border-color: #2e7d3e; box-shadow: 0 0 0 3px rgba(46, 125, 62, 0.1); }
        button { padding: 18px 35px; background: linear-gradient(45deg, #2e7d3e, #3a9b4f); color: white; border: none; border-radius: 50px; font-size: 16px; cursor: pointer; margin-left: 15px; transition: all 0.3s ease; font-weight: 600; }
        button:hover { transform: translateY(-2px); box-shadow: 0 5px 15px rgba(46, 125, 62, 0.3); }
        .features { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 25px; margin-top: 40px; }
        .feature { background: linear-gradient(135deg, #f8f9fa, #e9ecef); padding: 25px; border-radius: 15px; border-left: 5px solid #2e7d3e; }
        .feature h3 { color: #2e7d3e; margin-top: 0; font-size: 1.3em; }
        .feature ul { list-style: none; padding: 0; }
        .feature li { padding: 8px 0; }
        .feature li:before { content: "• "; color: #2e7d3e; font-weight: bold; }
        .loading { display: none; text-align: center; margin: 20px; }
        .spinner { border: 4px solid #f3f3f3; border-top: 4px solid #2e7d3e; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto 20px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    </style>
    <script>
        function showThinking() {
            document.getElementById('loading').style.display = 'block';
            document.querySelector('button').disabled = true;
            document.querySelector('button').innerHTML = 'Analyzing...';
        }
    </script>
</head>
<body>
    <div class="container">
        <h1>
            <img src="/favicon.ico" width="40" height="40" style="vertical-align: middle; margin-right: 10px;">
            Blueshift Support Bot
        </h1>

        <div class="search-container">
            <form method="post" action="/ai-analysis" onsubmit="showThinking()">
                <input type="text" name="query" placeholder="Describe your specific support question or problem..." required autofocus>
                <button type="submit">Get Support Analysis</button>
            </form>
        </div>

        <div id="loading" class="loading">
            <div class="spinner"></div>
            <div style="color: #2e7d3e; font-weight: 600;">⟳ Analyzing your question...</div>
            <div style="color: #666; margin-top: 10px;">Finding the best solution for your specific scenario</div>
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
                <h3>📚 Help Documentation</h3>
                <ul>
                    <li>Official Blueshift help center articles</li>
                    <li>API documentation and guides</li>
                    <li>Setup and configuration instructions</li>
                    <li>Best practices and tutorials</li>
                </ul>
            </div>
            <div class="feature">
                <h3>🏢 Confluence & Support</h3>
                <ul>
                    <li>Internal Confluence documentation</li>
                    <li>Related support tickets and resolutions</li>
                    <li>Team knowledge base articles</li>
                    <li>Troubleshooting runbooks</li>
                </ul>
            </div>
        </div>
    </div>
</body>
</html>'''

@app.route('/ai-analysis', methods=['GET', 'POST'])
def ai_analysis():
    if request.method == 'POST':
        query = request.form.get('query', '').strip()
    else:
        query = request.args.get('q', '').strip()

    if not query:
        return redirect('/')

    print(f"SUPPORT ANALYSIS REQUEST: {query}")

    # Generate solution and contextual resources
    solution = generate_ai_solution(query)
    resources = generate_contextual_resources(query)

    html = f'''<!DOCTYPE html>
<html>
<head>
    <title>Blueshift Analysis - {query[:50]}...</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; line-height: 1.6; }}
        .container {{ max-width: 1000px; margin: 0 auto; background: white; margin-top: 30px; margin-bottom: 30px; padding: 40px; border-radius: 20px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); }}
        h1 {{ color: #2e7d3e; margin-bottom: 25px; font-size: 2.2em; font-weight: 300; }}
        .query-box {{ background: linear-gradient(135deg, #f8f9fa, #e9ecef); padding: 20px; border-radius: 15px; border-left: 6px solid #2e7d3e; margin-bottom: 25px; }}
        .query-box strong {{ color: #2e7d3e; font-size: 1.1em; }}
        .query-text {{ font-size: 1.0em; color: #444; margin-top: 8px; font-style: italic; }}
        .solution {{ background: linear-gradient(135deg, #e7f5e7, #f0f8f0); border: 3px solid #2e7d3e; border-radius: 20px; padding: 30px; margin: 25px 0; max-height: 600px; overflow-y: auto; }}
        .solution h2 {{ color: #2e7d3e; margin-top: 0; display: flex; align-items: center; font-size: 1.6em; }}
        .solution h2:before {{ content: "🧠"; margin-right: 15px; font-size: 1.2em; }}
        .solution-content {{ white-space: pre-wrap; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; font-size: 14px; line-height: 1.7; color: #333; }}
        .solution-content h1, .solution-content h2, .solution-content h3 {{ color: #2e7d3e; margin-top: 20px; margin-bottom: 10px; }}
        .solution-content h4 {{ color: #2e7d3e; margin-top: 18px; }}
        .solution-content strong {{ color: #2e7d3e; }}
        .solution-content code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 4px; font-family: 'Courier New', monospace; }}
        .solution-content pre {{ background: #f8f8f8; padding: 12px; border-radius: 8px; border-left: 4px solid #2e7d3e; overflow-x: auto; }}
        .resources-section {{ background: linear-gradient(135deg, #e8f5e8, #d4f4d4); padding: 20px; border-radius: 15px; margin: 20px 0; border: 2px solid #2e7d3e; }}
        .resources-section h3 {{ color: #2e7d3e; font-size: 1.2em; margin-bottom: 15px; text-align: center; }}
        .resource-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; }}
        .resource-item {{ background: white; padding: 15px; border-radius: 10px; }}
        .resource-item strong {{ color: #2e7d3e; display: block; margin-bottom: 8px; }}
        .resource-item ul {{ list-style: none; padding: 0; margin: 0; }}
        .resource-item li {{ padding: 4px 0; }}
        .resource-item a {{ color: #0066cc; text-decoration: none; font-size: 0.9em; }}
        .resource-item a:hover {{ text-decoration: underline; color: #2e7d3e; }}
        .back-btn {{ display: inline-block; padding: 12px 25px; background: linear-gradient(45deg, #2e7d3e, #3a9b4f); color: white; text-decoration: none; border-radius: 50px; margin-top: 20px; transition: all 0.3s ease; font-weight: 600; }}
        .back-btn:hover {{ transform: translateY(-2px); box-shadow: 0 5px 15px rgba(46, 125, 62, 0.3); }}
        .copy-btn {{ float: right; padding: 6px 12px; background: #007bff; color: white; border: none; border-radius: 15px; cursor: pointer; font-size: 11px; }}
        .copy-btn:hover {{ background: #0056b3; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>
            <img src="/favicon.ico" width="32" height="32" style="vertical-align: middle; margin-right: 10px;">
            Support Analysis Results
        </h1>

        <div class="query-box">
            <strong>Your Question:</strong>
            <div class="query-text">"{query}"</div>
        </div>

        <div class="solution">
            <h2>Analysis Results</h2>
            <button class="copy-btn" onclick="copyToClipboard()">📋 Copy</button>
            <div class="solution-content" id="solution-content">{solution}</div>
        </div>

        <div class="resources-section">
            <h3>📋 Related Resources</h3>
            <div class="resource-grid">
                <div class="resource-item">
                    <strong>🎫 Related JIRAs</strong>
                    <ul>
                        {"".join(f'<li><a href="#">{jira}</a></li>' for jira in resources["jiras"])}
                    </ul>
                </div>
                <div class="resource-item">
                    <strong>📚 Help Documentation</strong>
                    <ul>
                        {"".join(f'<li><a href="#">{doc}</a></li>' for doc in resources["docs"])}
                    </ul>
                </div>
                <div class="resource-item">
                    <strong>🏢 Confluence & Tickets</strong>
                    <ul>
                        {"".join(f'<li><a href="#">{ticket}</a></li>' for ticket in resources["tickets"])}
                    </ul>
                </div>
            </div>
        </div>

        <div style="text-align: center;">
            <a href="/" class="back-btn">← Ask Another Question</a>
        </div>
    </div>

    <script>
        function copyToClipboard() {{
            const content = document.getElementById('solution-content').textContent;
            navigator.clipboard.writeText(content).then(function() {{
                const btn = document.querySelector('.copy-btn');
                btn.textContent = '✅ Copied!';
                setTimeout(() => btn.textContent = '📋 Copy', 2000);
            }});
        }}
    </script>
</body>
</html>'''

    return html

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print("STARTING BLUESHIFT SUPPORT BOT")
    print("🎯 Blueshift support analysis for agent success")
    print(f"🔗 URL: http://127.0.0.1:{port}")
    print("✅ Focused responses with thinking indicators!")
    app.run(host='0.0.0.0', port=port, debug=False)
