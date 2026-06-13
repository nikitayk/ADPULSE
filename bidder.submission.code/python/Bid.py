# Imports
import os
import time
import csv
import random
import warnings

import pandas as pd
import joblib  # For saving and loading models efficiently

from BidRequest import BidRequest
from Bidder import Bidder

# Silence sklearn/lightgbm version + convergence warnings (models trained on sklearn 1.2.2)
warnings.filterwarnings("ignore")

def xtract_device_family(user_agent_str):
    """Simple Function to parse user agent & extract Device Family, should we choose to use it as a feature"""
    if isinstance(user_agent_str, str):
        user_agent_lower = user_agent_str.lower()
        if (
            "mobile" in user_agent_lower
            or "android" in user_agent_lower
            or "iphone" in user_agent_lower
            or "ipad" in user_agent_lower
        ):
            if "ipad" in user_agent_lower:
                return "Tablet"
            else:
                return "Mobile"  # Covers Android phones, iPhones, general mobile
        elif (
            "tablet" in user_agent_lower or "ipad" in user_agent_lower
        ):  # Explicit tablet check (though 'ipad' already handled above)
            return "Tablet"
        elif (
            "windows" in user_agent_lower
            or "macintosh" in user_agent_lower
            or "linux" in user_agent_lower
        ):
            return "Desktop"  # Assume desktop if OS keywords are present and not mobile/tablet
        else:
            return "Other"  # Catch-all for unknown device types
    return "Unknown"

def xtract_os_family(user_agent_str):
    """Simple Function to parse user agent & extract OS Family, should we choose to use it as a feature"""
    if isinstance(user_agent_str, str):
        user_agent_lower = user_agent_str.lower()

        if "windows" in user_agent_lower:
            return "Windows"
        elif "macintosh" in user_agent_lower or "macos" in user_agent_lower:
            return "MacOS"
        elif "android" in user_agent_lower:
            return "Android"
        elif (
            "ios" in user_agent_lower
            or "iphone" in user_agent_lower
            or "ipad" in user_agent_lower
        ):
            return "iOS"
        elif "linux" in user_agent_lower:
            return "Linux"
        else:
            return "Other"
    return "Unknown"

def xtract_browser_family(user_agent_str):
    """Simple Function to parse user agent & extract Browser Family, should we choose to use it as a feature"""
    if isinstance(user_agent_str, str):
        user_agent_lower = user_agent_str.lower()

        if "chrome" in user_agent_lower:
            return "Chrome"
        elif "firefox" in user_agent_lower:
            return "Firefox"
        elif "safari" in user_agent_lower and "chrome" not in user_agent_lower:
            # Safari check, but exclude Chrome (which can also contain 'Safari')
            return "Safari"
        elif "edge" in user_agent_lower:
            return "Edge"
        elif "msie" in user_agent_lower or "trident" in user_agent_lower:
            return "IE"
        else:
            return "Other"
    return "Unknown"

def xtract_network_class(network_class_str):
    if isinstance(network_class_str, str):
        first_octet = int(network_class_str.split('.')[0])
        if first_octet in range(1,126):
            return 1
        elif first_octet in range(128,191):
            return 2
        elif first_octet in range(192,223):
            return 3
        elif first_octet in range(224, 239):
            return 4
        elif first_octet in range(242,255):
            return 5

    return 0


class Bid(Bidder):

    def __init__(self):
        """Initializes the bidder parameters and loads models."""
        self.bidRatio = 90  # Increased bid ratio
        self.baseBidPrice = 50 # Base bid price
        # Resolve model paths relative to this file so loading works from any CWD
        _here = os.path.dirname(os.path.abspath(__file__))
        self.ctr_model = joblib.load(os.path.join(_here, "model_ctr.pkl"))
        self.cvr_model = joblib.load(os.path.join(_here, "model_cvr.pkl"))
        self.scaler_ctr = joblib.load(os.path.join(_here, "scaler_ctr.pkl"))
        self.scaler_cvr = joblib.load(os.path.join(_here, "scaler_cvr.pkl"))
        self.advertiser_n_values = {
            1458: 0,
            3358: 2,
            3386: 0,
            3427: 0,
            3476: 10
        }
        self.totalBudget = 625000
        self.spentBudget = 0

    # feature engineering
    def _preprocess_bid_request_ctr(self, bidRequest: BidRequest) -> pd.DataFrame:
        """Preprocesses a single bid request into a DataFrame for CTR model."""
        bid_request_data = {
            'ua_browser': xtract_browser_family(bidRequest.getUserAgent()),
            'ua_device': xtract_device_family(bidRequest.getUserAgent()),
            'ua_os': xtract_os_family(bidRequest.getUserAgent()),
            'weekday': pd.to_datetime(bidRequest.getTimestamp(), format="%Y%m%d%H%M%S%f").weekday(),
            'AdvertiserID': int(bidRequest.getAdvertiserId()),
            'Payingprice': 0,  # Not available in bid request, using 0 or mean from training data might be better
            'Adslotfloorprice': int(bidRequest.getAdSlotFloorPrice() or 0), # Handle None/Null values
            'Adslotformat': bidRequest.getAdSlotFormat(),
            'Adslotheight': int(bidRequest.getAdSlotHeight()),
            'Adslotvisibility': bidRequest.getAdSlotVisibility(),
            'Adslotwidth': int(bidRequest.getAdSlotWidth()),
            'Timestamp': float(bidRequest.getTimestamp())
        }
        df = pd.DataFrame([bid_request_data])

        # Convert categorical features to numerical codes, handling potential missing categories
        for col in ['ua_device', 'ua_os', 'ua_browser']:
            df[col] = df[col].astype('category').cat.codes

        df["Adslotvisibility"] = df["Adslotvisibility"].astype("category").cat.codes
        df["Adslotformat"] = df["Adslotformat"].astype("category").cat.codes

        return df

    def _preprocess_bid_request_cvr(self, bidRequest: BidRequest) -> pd.DataFrame:
        """Preprocesses a single bid request into a DataFrame for CVR model."""
        bid_request_data = {
            'clicked': 0, # Not available in bid request, assuming 0
            'ua_os': xtract_os_family(bidRequest.getUserAgent()),
            'ua_device': xtract_device_family(bidRequest.getUserAgent()),
            'weekday': pd.to_datetime(bidRequest.getTimestamp(), format="%Y%m%d%H%M%S%f").weekday(),
            'Timestamp': float(bidRequest.getTimestamp()),
            'AdvertiserID': int(bidRequest.getAdvertiserId()),
            'Payingprice': 0, # Not available in bid request
            'Adexchange': int(bidRequest.getAdExchange() or 0), # Handle None/Null values
            'Biddingprice': 0, # Not available in bid request
            'Adslotformat': bidRequest.getAdSlotFormat(),
            'Adslotheight': int(bidRequest.getAdSlotHeight()),
            'Region': int(bidRequest.getRegion() or 0) # Handle None/Null values
        }
        df = pd.DataFrame([bid_request_data])

        # Convert categorical features to numerical codes, handling potential missing categories
        for col in ['ua_device', 'ua_os']:
            df[col] = df[col].astype('category').cat.codes
        df["Adslotformat"] = df["Adslotformat"].astype("category").cat.codes

        return df

    def getBidPrice(self, bidRequest: BidRequest) -> int:
        """
        Predicts CTR and CVR using loaded models and calculates bid price.
        Returns bid price or -1 if no bid is placed.
        """
        bidPrice = -1

        # Hard stop if budget exhausted
        if self.spentBudget >= self.totalBudget:
            return -1

        if random.randint(0, 99) < self.bidRatio:

            # Preprocess and predict CTR
            ctr_features_df = self._preprocess_bid_request_ctr(bidRequest)
            ctr_features_scaled = self.scaler_ctr.transform(ctr_features_df)
            ctr_prediction = self.ctr_model.predict_proba(ctr_features_scaled)[:, 1][0]

            # Preprocess and predict CVR
            cvr_features_df = self._preprocess_bid_request_cvr(bidRequest)
            cvr_features_scaled = self.scaler_cvr.transform(cvr_features_df)
            cvr_prediction = self.cvr_model.predict_proba(cvr_features_scaled)[:, 1][0]

            # Get advertiser specific N value
            advertiser_id = int(bidRequest.getAdvertiserId())
            n_value = self.advertiser_n_values.get(advertiser_id, 1)

            # Bid price formula: base_bid x CTR x (1 + N x CVR)
            estimated_value = ctr_prediction * (1 + n_value * cvr_prediction)
            bidPrice = int(self.baseBidPrice * estimated_value)

            # Enforce floor price
            floor_price = int(bidRequest.getAdSlotFloorPrice() or 0)
            bidPrice = max(bidPrice, floor_price)

            # Validity checks
            if bidPrice <= 0:
                bidPrice = -1
            elif bidPrice > 300:
                bidPrice = 300

            # Track spend and dynamically adjust bidRatio
            if bidPrice != -1:
                self.spentBudget += bidPrice

                remaining_ratio = (self.totalBudget - self.spentBudget) / self.totalBudget

                if remaining_ratio < 0.1:
                    self.bidRatio = 20
                elif remaining_ratio < 0.2:
                    self.bidRatio = 50
                else:
                    self.bidRatio = 90

        return bidPrice

    def getBidRequest(self, bid_data, bidder_instance):
        """
        Processes a single bid request and measures execution time.
        """
        bid_request = BidRequest() # instantiate

        bid_request.setBidId(bid_data[0])
        
        # Timestamp Handling (Debugging + Fix)
        timestamp_str = bid_data[1]
        try:
            pd.to_datetime(timestamp_str, format="%Y%m%d%H%M%S%f")  # validate format
        except ValueError:
            print(f"Invalid timestamp: {timestamp_str}")

        bid_request.setTimestamp(timestamp_str)
        bid_request.setVisitorId(bid_data[2] if bid_data[2] != 'null' else None)
        bid_request.setUserAgent(bid_data[3])
        bid_request.setIpAddress(bid_data[4])
        bid_request.setRegion(bid_data[5] if bid_data[5] != 'null' else None)
        bid_request.setCity(bid_data[6] if bid_data[6] != 'null' else None)
        bid_request.setAdExchange(bid_data[7] if bid_data[7] != 'null' else None)
        bid_request.setDomain(bid_data[8] if bid_data[8] != 'null' else None)
        bid_request.setUrl(bid_data[9] if bid_data[9] != 'null' else None)
        bid_request.setAnonymousURLID(bid_data[10] if bid_data[10] != 'null' else None)
        bid_request.setAdSlotID(bid_data[11] if bid_data[11] != 'null' else None)
        bid_request.setAdSlotWidth(bid_data[12] if bid_data[12] != 'null' else None)
        bid_request.setAdSlotHeight(bid_data[13] if bid_data[13] != 'null' else None)
        bid_request.setAdSlotVisibility(bid_data[14] if bid_data[14] != 'null' else None)
        bid_request.setAdSlotFormat(bid_data[15] if bid_data[15] != 'null' else None)
        bid_request.setAdSlotFloorPrice(bid_data[16] if bid_data[16] != 'null' else None)
        bid_request.setCreativeID(bid_data[17] if bid_data[17] != 'null' else None)
        bid_request.setAdvertiserId(bid_data[18] if bid_data[18] != 'null' else None)
        bid_request.setUserTags(bid_data[19] if bid_data[19] != 'null' else None)

        # Execution time measurement
        start_time = time.time()
        bid_price = bidder_instance.getBidPrice(bid_request)
        end_time = time.time()
        execution_time = (end_time - start_time) * 1000  # Convert to milliseconds

        # Debugging print
        print(f"Processed bid request with ID: {bid_data[0]}, from {bid_request.advertiserId} Execution Time: {execution_time:.4f} ms, at ${bid_price}/CPM ")

        with open("./output.txt", 'a') as f:
            writer = csv.writer(f)
            writer.writerow([timestamp_str, bid_data[0], bid_request.advertiserId, execution_time, bid_price])

        return execution_time, 1 if bid_price != -1 else 0, 1 if bid_price == -1 else 0

    def test_bidding_framework(self, bid_dataframe, no_of_rows):
        """ Tests the Bid class with bid requests and measures execution time.  """
        bidder_instance = Bid()
        total_execution_time = 0
        bid_count = 0
        no_bid_count = 0

        # Apply function row-wise and store results
        results = bid_dataframe.head(no_of_rows).apply(lambda row: self.getBidRequest(row, bidder_instance), axis=1)
        
        # Aggregate results
        total_execution_time = sum(result[0] for result in results)
        bid_count = sum(result[1] for result in results)
        no_bid_count = sum(result[2] for result in results)

        # Calculate average execution time
        avg_execution_time = total_execution_time / (bid_count + no_bid_count) if (bid_count + no_bid_count) > 0 else 0

        print("\n--- Testing Summary ---")
        print(f"Total Bid Requests Processed: {bid_count + no_bid_count}")
        print(f"Number of Bids Placed: {bid_count}")
        print(f"Number of No Bids: {no_bid_count}")
        print(f"Average Execution Time per Bid Request: {avg_execution_time:.4f} ms")


# runner code (only executes when run directly: `python Bid.py`, not on import)
if __name__ == "__main__":
    test_df = pd.read_csv("bid.07.txt", sep='\t', header=None, na_values=["null"])
    test_bid = Bid()
    test_df.info()
    test_bid.test_bidding_framework(test_df, 1000)
