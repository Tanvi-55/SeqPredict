# Backend - FastAPI BLAST Server

## Overview

This backend is built using FastAPI and provides endpoints to perform BLAST operations, protein sequence generation, structure prediction, protein analysis, and ORF finding.

## Setup Instructions

1. **Navigate to the backend directory**:

    ```bash
    cd backend
    ```

2. **Create a virtual environment** (optional but recommended):

    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3. **Install dependencies**:

    ```bash
    pip install -r requirements.txt
    ```

4. **Configure environment variables**:

    Ensure the `.env` file is present with the required variables.

5. **Run the server**:

    ```bash
    uvicorn main:app --host 0.0.0.0 --port 8000
    ```

    The server will be accessible at `http://localhost:8000`.

## API Endpoints

- **Health Check**: `GET /`
- **Submit BLAST Job**: `POST /blast/submit`
- **Check BLAST Status**: `GET /blast/status/{rid}`
- **Retrieve BLAST Result**: `GET /blast/result/{rid}`
- **Poll and Retrieve BLAST Result**: `POST /blast/poll-and-retrieve`
- **Generate Protein Sequence**: `POST /protein/generate`
- **Mock Structure Prediction**: `POST /protein/mock_structure`
- **Run NR BLAST Search**: `POST /blast/run_nr`
- **Run PDB BLAST Search**: `POST /blast/run_pdb`
- **Run SwissProt BLAST Search**: `POST /blast/run_swissprot`
- **Analyze Protein Sequence**: `POST /protein/analyze`
- **Find ORFs in DNA Sequence**: `POST /orf/find`
