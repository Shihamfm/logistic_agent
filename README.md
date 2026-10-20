# Logistic Agent

## Source:
### Data: https://www.kaggle.com/datasets/datasetengineer/logistics-and-supply-chain-dataset?resource=download

### Ingesting data:
- Download the dataset from 'data\dynamic_supply_chain_logistics_dataset.csv'
- Create EC2 instance > docker container > mcr.microsoft.com/mssql/server:2022-latest
- Spin up the Legacy MSSQL Server

- Create docker container
```
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=FDElogistic321!" -p 1433:1433 --name legacy-mssql -d mcr.microsoft.com/mssql/server:2022-latest
```
- Check the image
```
docker ps -a
```

- Run the container in docker.desktop after that run the below script
```
python scripts\ingest_legacy_data.py
```
### Connecting to the data
- Use VS code extention : "SQL Server (mssql) by microsoft
#### Add connection to local
```
Profile Name: supply_chain
Server name*: localhost
Port: 1433
Trust server certificate: turn it ON (Crucial for Docker)
Authentication type*: SQL Login
User name*: SA
Password*: FDElogistic321!
Save Password: Check this box
Database name: Type master (or leave it on "Select a database")
Encrypt: Change this from Mandatory to Optional (or False)
```

#### Add connection to cloud (AWS)

- Create EC2 instance
1. Platform: Ubuntu
2. Instance type: t3.micro
3. Create new key pair
4. Select existing security group ( After creating new security group - Inbound rules -2 for (MSSQL & SSH) and Outbound rules for All traffic )
5. Configure storage - 30 GB
6. File system - None
7. Launch instance


- Connect to a Linux EC2 instance

1. Open the local terminal - cmd
```
ssh -i /path/to/your-key.pem username@your-ec2-public-ip
```
2. Update the system package manager
```
sudo yum update -y 
```
3. Install and start Docker
```
sudo yum install docker -y
```
4. Create docker container
```
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=FDElogistic321!" -p 1433:1433 --name legacy-mssql -d mcr.microsoft.com/mssql/server:2022-latest
```
5. Check the image
```
docker ps -a
```
6. Run the script after change the SQL_SERVER_HOST to cloud IP address
```
python scripts\ingest_legacy_data.py
```
```
Profile Name: cloud_supply_chain
Server name*: 15.135.187.245 (Derived the IP from created public IP address from created EC2 instance)
Port: 1433
Trust server certificate: turn it ON (Crucial for Docker)
Authentication type*: SQL Login
User name*: SA
Password*: FDElogistic321!
Save Password: Check this box
Database name: Type master (or leave it on "Select a database")
Encrypt: Change this from Mandatory to Optional (or False)
```
