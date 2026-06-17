# URL Health Check Automation

## Overview

This project automates health monitoring of application URLs across multiple environments. The solution continuously validates endpoint availability, response status, and service health, reducing manual monitoring effort and improving incident response time.

## Features

* Automated monitoring of 500+ application URLs
* Real-time HTTP/HTTPS health validation
* Status code verification
* Detailed health check reports
* Automated logging and alert generation
* Reduced manual monitoring effort
* Improved MTTR (Mean Time To Resolution)

## Tech Stack

* Bash Shell Scripting
* Linux
* Cron Scheduler
* HTTP/HTTPS Monitoring
* Log Management

## Project Architecture

1. Read URLs from configuration file.
2. Execute health checks at scheduled intervals.
3. Validate HTTP response codes.
4. Generate health reports.
5. Log failures and exceptions.
6. Trigger notifications for unavailable services.

## Benefits

* Reduced manual effort by 90%
* Faster outage detection
* Improved application availability monitoring
* Consistent health validation across environments
* Enhanced operational efficiency

## Repository Structure

├── scripts/
│   └── health_check.sh
├── config/
│   └── urls.txt
├── logs/
├── reports/
└── README.md

## Usage

```bash
chmod +x health_check.sh
./health_check.sh
```

## Future Enhancements

* Email notifications
* Slack/MS Teams integration
* Dashboard reporting
* Historical trend analysis

## Author

Swapnil Gajanan Bharsakle
DevOps Engineer
