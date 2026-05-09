import sumolib


net = sumolib.net.readNet('erlangen.net.xml')

stations_coords = [
    {"id": "CS_1", "lon": 11.048693, "lat": 49.591412}, ### 49,591412	11,048693
    {"id": "CS_2", "lon": 11.050456, "lat": 49.592576}, ### 49,592576	11,050456
    {"id": "CS_3", "lon": 11.050566, "lat": 49.592486}   ### 49,592486	11,050566
]

for s in stations_coords:
    # 1. Convert Lon/Lat to Network X,Y
    x, y = net.convertLonLat2XY(s['lon'], s['lat'])
    
    print(x,y)

# 49,59095	11,04544 
# 49,591412	11,048693
# 49,592576	11,050456
# 49,592486	11,050566
# 49,59289	11,054978 
