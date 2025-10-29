"use client"
import { useVisitCount } from "@/context/VisitCountContext";

const Footer = () => {
    const visitCount = useVisitCount();

    return (
        <footer className="footer">
            <div className="footer-elements">
                <a href="/tc" className="footer-element">Terms and Conditions</a>
                <a href="/pp" className="footer-element">Privacy Policy</a>
                {visitCount && <span className="footer-element">Visit Count: {visitCount}</span>}
            </div>
            <br />
            © 2024 StreamStorm. All rights reserved.
        </footer>
    );
};

export default Footer;
