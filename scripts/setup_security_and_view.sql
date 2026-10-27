-- FDE ENTERPRISE SECURITY & SEMANTIC LAYER SCRIPT
-- Purpose: Protect the legacy DB from LLM hallucinations and mutations

--- Create a dedicated schema for clean AI views
CREATE SCHEMA FDE_VIEWS;
GO -- GO only works in SQL Server Management Studio (SSMS) or Azure Data Studio (ADS)

--- Create the Sementic View (Translating legacy junk toclean English)
CREATE VIEW FDE_VIEWS.VW_ACTIVE_FLEET AS
SELECT
    TS_UTC AS [Timestamp],
    V_LAT AS [Latitude],
    V_LON AS [Longitude],
    CAST(IOT_TEMP_VAL_C AS FLOAT) AS [Current_Temperature_C],
    CGO_COND_CD AS [Cargo_Condition_Code],
    RISK_CLS_TXT AS [Risk_Classification],
    DELAY_PROB_DEC AS [Delay_Probability],
    PRT_CNG_LVL AS [Port_Congestion_Level],
    RT_RSK_IDX AS [Route_Risk_Index]
FROM dbo.supply_chain_hist_raw_data
GO


--- Create strict readonly login and user for the AI agent
CREATE LOGIN USR_FDE_RO WITH PASSWORD = 'FDEAgentPassword2026!';
CREATE USER USR_FDE_RO FOR LOGIN USR_FDE_RO;
GO

--- Grant access only to the sementic view, explicitly denying everything else
GRANT SELECT ON FDE_VIEWS.VW_ACTIVE_FLEET TO USR_FDE_RO;
DENY SELECT ON dbo.supply_chain_hist_raw_data TO USR_FDE_RO;
DENY INSERT, UPDATE, DELETE ON dbo.supply_chain_hist_raw_data TO USR_FDE_RO;
GO

--- Grant access to the audit log view, explicitly denying everything else
GRANT INSERT ON FDE_VIEWS.AgentAuditLog TO USR_FDE_RO;
DENY SELECT ON FDE_VIEWS.AgentAuditLog TO USR_FDE_RO;
DENY UPDATE, DELETE ON FDE_VIEWS.AgentAuditLog TO USR_FDE_RO;
GO

