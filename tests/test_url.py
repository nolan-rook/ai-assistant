import requests
from bs4 import BeautifulSoup

# Function definition from your setup
def extract_webpage_content(url):
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raises an HTTPError if the status is 4xx, 5xx
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract text from the webpage
        text = ' '.join(p.get_text() for p in soup.find_all('p'))
        return text
    except Exception as e:
        print(f"Error fetching webpage content: {e}")
        return None

# Test URLs
test_urls = [
    "https://baise.nl"
    # Add more test URLs as needed
]

# Iterate over the test URLs, fetch and print their content
for url in test_urls:
    print(f"Testing URL: {url}")
    content = extract_webpage_content(url)
    if content:
        print("Extracted Content:")
        print(content[:500])  # Print the first 500 characters to keep output manageable
    else:
        print("Failed to extract content.")
    print("------\n")