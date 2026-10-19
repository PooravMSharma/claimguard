"""Fictional people, places and organisations used to build synthetic claims."""

MALE_FIRST = ["Ramesh", "Suresh", "Amit", "Rahul", "Vikram", "Arjun", "Manoj", "Sanjay", "Rajesh", "Deepak",
              "Anil", "Mahesh", "Naveen", "Pradeep", "Rohit", "Ajay", "Kunal", "Harish", "Gopal", "Imran",
              "Farhan", "Joseph", "Gurpreet", "Venkatesh", "Karthik"]
FEMALE_FIRST = ["Sunita", "Priya", "Anjali", "Kavita", "Neha", "Pooja", "Meena", "Rekha", "Asha", "Lakshmi",
                "Divya", "Shalini", "Geeta", "Nisha", "Swati", "Farah", "Mary", "Harpreet", "Lavanya", "Sneha"]
LAST = ["Sharma", "Verma", "Gupta", "Singh", "Kumar", "Agarwal", "Jain", "Meena", "Choudhary", "Yadav",
        "Patel", "Shah", "Reddy", "Nair", "Iyer", "Khan", "Das", "Mishra", "Joshi", "Saxena", "Rathore",
        "Bhatt", "Pillai", "Menon", "D'Souza"]

# (city, state, CGHS tier)
CITIES = [
    ("Jaipur", "Rajasthan", "tier_2"), ("Delhi", "Delhi", "tier_1"), ("Mumbai", "Maharashtra", "tier_1"),
    ("Pune", "Maharashtra", "tier_1"), ("Lucknow", "Uttar Pradesh", "tier_2"), ("Indore", "Madhya Pradesh", "tier_2"),
    ("Ahmedabad", "Gujarat", "tier_1"), ("Kota", "Rajasthan", "tier_3"), ("Udaipur", "Rajasthan", "tier_3"),
    ("Bengaluru", "Karnataka", "tier_1"), ("Kochi", "Kerala", "tier_2"), ("Nagpur", "Maharashtra", "tier_2"),
]

HOSPITAL_PREFIX = ["Shree", "Sanjeevani", "Apex", "Lifeline", "Sunrise", "Metro", "Arogya", "City Care",
                   "Ganga", "Sai", "Medicity", "Narayan", "Pragati", "Unity", "Krishna"]
HOSPITAL_SUFFIX = ["Hospital", "Multispeciality Hospital", "Hospital & Research Centre", "Medical Centre",
                   "Superspeciality Hospital", "Nursing Home"]
STREETS = ["MG Road", "Station Road", "Civil Lines", "Malviya Nagar", "Vaishali Nagar", "Sector 14",
           "Ring Road", "Gandhi Path", "Tonk Road", "Link Road", "Nehru Nagar", "Model Town"]

COUNCILS = [("RMC", "Rajasthan Medical Council"), ("DMC", "Delhi Medical Council"),
            ("MMC", "Maharashtra Medical Council"), ("UPMC", "Uttar Pradesh Medical Council"),
            ("MPMC", "Madhya Pradesh Medical Council"), ("GMC", "Gujarat Medical Council"),
            ("KMC", "Karnataka Medical Council"), ("TCMC", "Travancore Cochin Medical Council"),
            ("NMC", "National Medical Commission")]

INSURERS = ["Bharat General Insurance (fictional)", "Suraksha Health Insurance (fictional)",
            "Navjeevan General Insurance (fictional)"]
TPAS = ["MediAssist-like TPA (fictional)", "HealthFirst TPA (fictional)", "CareLink TPA (fictional)"]
PRODUCTS = [("SHP-IND", "Swasthya Individual Plan"), ("SHP-FAM", "Swasthya Family Floater"),
            ("GRP-EMP", "Group Employee Health Cover")]

LABS_IN_HOUSE = "Department of Laboratory Medicine"
POLICE_STATIONS = ["Kotwali", "Civil Lines", "Sadar Bazar", "Malviya Nagar", "Sector 5", "City Kotwali"]