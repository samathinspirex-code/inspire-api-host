import asyncio
from app.core.database import engine, Base
from app.modules.crm.models.lead import CrmLead
from app.modules.crm.models.activity import CrmActivity
from sqlalchemy import text


async def setup_crm():
    print("Connecting to database...")
    async with engine.begin() as conn:
        res = await conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        )
        tables = [r[0] for r in res.fetchall()]
        print("Existing tables in DB:", tables)

        print("Creating CRM tables if they don't exist...")
        await conn.run_sync(Base.metadata.create_all)

        res2 = await conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        )
        tables2 = [r[0] for r in res2.fetchall()]
        print("Tables in DB after create_all:", tables2)

        # Check count of crm_leads
        lead_count = await conn.scalar(text("SELECT COUNT(*) FROM crm_leads"))
        print(f"Current crm_leads count: {lead_count}")

        if lead_count == 0:
            print("Seeding initial dummy leads into crm_leads table...")
            # Seed the initial dummy leads so they persist across refreshes!
            initial_leads = [
                ("Samindi Weerasiri", "samindi.inspirex@gmail.com", "076-694220", "760694220", "Colombo", "Western", "Helena Girls's School", "Sri Lankan", "School of Computing", "School of Computing", "Level 3 Diploma in Smart Computing", "After O/L ( Pending Results)", "counselling", "website_admission", "high", "Samindi Weerasiri", 10000.0, "Waiting for Parental Approval"),
                ("Oshaka", "oshaka@gmail.com", "766534112", "766534112", "Gampaha", "Western", "", "Sri Lankan", "School of Computing", "School of Computing", "Diploma in Software Engineering with AI", "A/L", "lost_deferred", "whatsapp", "low", "Navodi Rathnayake", None, "Not Interested / Declined"),
                ("Samal Dimalye", "samaldi.malye@gmail.com", "23052740400", "23052740400", "Port Louis", "", "", "Mauritius", "School of Psychology", "School of Psychology", "Level 4 Diploma in Psychology", "Diploma level 3", "enrolled", "whatsapp", "high", "Navodi Rathnayake", 12000.0, "Payment Done"),
                ("Kirusha Paramenthiran", "kirushaparamenthiran@gmail.com", "750704864", "750704864", "Jaffna", "Northern", "Mahajana College Tellippalai", "Sri Lankan", "School of Business", "School of Business", "Level 4 Diploma in Business Management", "A/L", "enrolled", "meta", "high", "Ameera Zanhar", 10000.0, "Payment Done"),
                ("Oshadhi Tharanga", "oshadhi@gmail.com", "773018164", "773018164", "Gampaha", "Western", "", "Sri Lankan", "School of Computing", "School of Computing", "Level 4 Diploma in Software Engineering", "After A/L( Pending Results)", "application_started", "website_contact", "medium", "Ameera Zanhar", None, "Future Prospect"),
                ("Mohommad Ishaq", "mhdishaq10@gmail.com", "764140618", "764140618", "Colombo", "Western", "", "Sri Lankan", "School of Business", "School of Business", "Level 4 Diploma in Business Management", "A/L", "contacted", "website_admission", "medium", "Navodi Rathnayake", None, "Uncontactable / No Info"),
                ("Nawanjana Amandi", "amandinawanjana160@gmail.com", "767023644", "767023644", "Colombo", "Western", "", "Sri Lankan", "School of Business", "School of Business", "Level 5 Diploma in Business Management", "Diploma level 4", "counselling", "meta", "medium", "Samindi Weerasiri", None, "Exploring Other Options"),
                ("Parameshwaran Greashmikaa", "kreashmiparthiban@gmail.com", "94760481734", "94760481734", "Colombo", "Western", "", "Sri Lankan", "School of Computing", "School of Computing", "Diploma in Software Engineering", "Completed O/Ls", "new_inquiry", "meta", "high", "Heath Fernando", None, "Potential Lead"),
                ("Avelon Senn", "avelon@gmail.com", "766313002", "766313002", "Negombo", "Western", "", "Sri Lankan", "School of Computing", "School of Computing", "Level 5 Diploma in Software Engineering", "Diploma level 4", "contacted", "website_contact", "low", None, None, "Uncontactable / No Info"),
            ]
            for row in initial_leads:
                await conn.execute(text("""
                    INSERT INTO crm_leads (
                        full_name, email, phone, whatsapp, city, district, school,
                        nationality, faculty, interested_programme, interested_course,
                        highest_qualification, stage, source, priority, counsellor_name,
                        amount, student_status, is_archived, created_at, updated_at
                    ) VALUES (
                        :full_name, :email, :phone, :whatsapp, :city, :district, :school,
                        :nationality, :faculty, :interested_programme, :interested_course,
                        :highest_qualification, :stage, :source, :priority, :counsellor_name,
                        :amount, :student_status, false, now(), now()
                    )
                """), {
                    "full_name": row[0], "email": row[1], "phone": row[2], "whatsapp": row[3],
                    "city": row[4], "district": row[5], "school": row[6], "nationality": row[7],
                    "faculty": row[8], "interested_programme": row[9], "interested_course": row[10],
                    "highest_qualification": row[11], "stage": row[12], "source": row[13],
                    "priority": row[14], "counsellor_name": row[15], "amount": row[16],
                    "student_status": row[17],
                })
            print(f"Seeded {len(initial_leads)} leads successfully!")

    await engine.dispose()
    print("Done!")

if __name__ == "__main__":
    asyncio.run(setup_crm())
