import sys

sys.path.insert(0, r"G:\Automate\Cursor\rowan-gis-chatbot")

from services.rowan_address_search import search_rowan_address

result = search_rowan_address("550 MT HALL RD")
print(result)
