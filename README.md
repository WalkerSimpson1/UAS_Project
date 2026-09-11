# UAS Project | LegiScan and USASpending data ETL pipeline + SQLite Database
<img width="515" height="160" alt="UASGithub" src="https://github.com/user-attachments/assets/f1aeec86-f5f4-4966-ad35-291711132e80" />

---
## **This is my public GitHub repository where...**    
- The live SQL database .db file of my UAS policy tracking lives.    
- The version history of my SQL file can be found via GitHub commit history of the exported CSV file.    
- One can find the notebooks used to query, transform, and ingest data from LegiScan API and USASpending.gov into the SQL database.    

## **Info + Locations**      
     
**Schedule Info**         
I run the legiscan ETL notebooks weekly, as the getDataset section I am using updates every Sunday. Therefore, the Exported legislation CSV changes every week when I run the notebooks. Historical versions are available under my commit history.    
     
I update the legislation and rulemaking databases with my email alerts from congress.gov and federal register saved searches manually as well. 

**Databases**    
SQL Database - *UAS_Tracker.db*    
CSV Export of UAS_Tracker.db for easy historial viewing - *Exported_SQL_Legislation_Table.csv*    
Raw data from LegiScan API before extraction and cleaning - *folder/legiscan_getDatasetRaw_data*    
USASpending data for analysis - *folder/Spending.gov downloaded data*    
    
**Code**    
Legiscan API ETL - *folder/notebooks*    




